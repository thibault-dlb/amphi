"""Synchronisation des devoirs Moodle vers le calendrier iCloud.

`sync()` est une logique pure (testable avec un faux calendrier) : Moodle est la
source de vérité, les événements Amphi existants sont réécrits si leur empreinte
diffère. `run_sync()` est le point d'entrée utilisé par l'interface.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from .client import MoodleError, MoodleTokenError, load_token, MoodleClient
from .fichiers import documents_a_recuperer
from .model import UID_PREFIX, Devoir, EventInfo, EventSpec

log = logging.getLogger(__name__)

PARIS = ZoneInfo("Europe/Paris")
ALARMS = [timedelta(days=3), timedelta(days=1), timedelta(hours=3)]
DEFAULT_BASE_URL = "https://moodle.ipsa.fr"
DEFAULT_CALENDAR = "Moodle IPSA"
Progress = Callable[[str], None]   # reçoit le libellé de l'étape en cours
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def _dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=PARIS)


def _as_paris(dt: datetime) -> datetime:
    """Rend un datetime aware (naïf = heure de Paris)."""
    return dt.replace(tzinfo=PARIS) if dt.tzinfo is None else dt


def _fmt_date(ts: int) -> str:
    return _dt(ts).strftime("%d/%m/%Y")


def _fmt_heure(ts: int) -> str:
    dt = _dt(ts)
    return f"{dt:%H}h{dt:%M}"


def _fmt_echeance(ts: int) -> str:
    """Ex. « jeu. 15/10 23h59 »."""
    dt = _dt(ts)
    return f"{JOURS[dt.weekday()]} {dt:%d/%m} {_fmt_heure(ts)}"


def _hash(summary: str, description: str, start: datetime, end: datetime, alarms: list[timedelta]) -> str:
    alarmes = ",".join(str(int(a.total_seconds())) for a in alarms)
    brut = "|".join([summary, description, start.isoformat(), end.isoformat(), alarmes])
    return hashlib.sha1(brut.encode("utf-8")).hexdigest()[:16]


def build_event_spec(d: Devoir) -> EventSpec:
    """Construit l'événement calendrier d'un devoir daté (échéance = fin, début = fin − 1 h)."""
    end = _dt(d.due)
    start = end - timedelta(hours=1)
    summary = f"{'✅' if d.rendu else '📝'} {d.nom} — {d.matiere}"

    lignes: list[str] = []
    if d.rendu:
        etat = "État : rendu"
        if d.rendu_le:
            etat += f" le {_fmt_date(d.rendu_le)} à {_fmt_heure(d.rendu_le)}"
        if d.fichiers:
            etat += " — " + ", ".join(d.fichiers)
        lignes.append(etat)
    else:
        etat = "État : non rendu"
        if d.cutoff:
            etat += f" (date limite stricte : {_fmt_date(d.cutoff)} {_fmt_heure(d.cutoff)})"
        lignes.append(etat)
    lignes.append(f"Déposer : {d.url}")
    if d.intro.strip():
        lignes += ["", "Consignes :", d.intro.strip()]
    description = "\n".join(lignes)

    alarms = [] if d.rendu else list(ALARMS)
    return EventSpec(
        uid=d.uid,
        summary=summary,
        description=description,
        url=d.url,
        start=start,
        end=end,
        alarms=alarms,
        hash=_hash(summary, description, start, end, alarms),
    )


class CalendarBackend(Protocol):
    def list_events(self) -> dict[str, EventInfo]: ...

    def put_event(self, spec: EventSpec) -> None: ...

    def delete_event(self, uid: str) -> None: ...


class NullCalendar:
    """Calendrier vide en mémoire : rien n'est lu ni écrit (simulation, iCloud non configuré)."""

    def list_events(self) -> dict[str, EventInfo]:
        return {}

    def put_event(self, spec: EventSpec) -> None:
        pass

    def delete_event(self, uid: str) -> None:
        pass


@dataclass
class SyncReport:
    ajoutes: list[str] = field(default_factory=list)
    mis_a_jour: list[str] = field(default_factory=list)
    deplaces: list[str] = field(default_factory=list)
    rendus: list[str] = field(default_factory=list)
    supprimes: list[str] = field(default_factory=list)
    en_retard: list[str] = field(default_factory=list)
    sans_date: list[str] = field(default_factory=list)
    erreurs: list[str] = field(default_factory=list)
    nouveaux_documents: list[str] = field(default_factory=list)
    cles_documents: list[str] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)
    dry_run: bool = False

    def has_changes(self) -> bool:
        return any((self.ajoutes, self.mis_a_jour, self.deplaces, self.rendus, self.supprimes))

    def to_text(self) -> str:
        lignes: list[str] = []
        if self.dry_run:
            lignes.append("[SIMULATION] Aucune modification écrite dans iCloud.")
        sections_agenda = [
            ("Nouveaux devoirs", self.ajoutes),
            ("Mis à jour", self.mis_a_jour),
            ("Échéances déplacées", self.deplaces),
            ("Devoirs rendus", self.rendus),
            ("Événements supprimés", self.supprimes),
            ("En retard", self.en_retard),
            ("Sans date limite", self.sans_date),
        ]
        sections_fin = [("Erreurs", self.erreurs), ("Avertissements", self.avertissements)]
        for titre, items in sections_agenda:
            if items:
                lignes.append(f"{titre} ({len(items)}) :")
                lignes.extend(f"  • {it}" for it in items)
        if self.nouveaux_documents:
            n = len(self.nouveaux_documents)
            lignes.append(f"Nouveaux documents sur Moodle ({n}) — lance /maj-moodle dans Claude Code :")
            lignes.extend(f"  • {it}" for it in self.nouveaux_documents)
        for titre, items in sections_fin:
            if items:
                lignes.append(f"{titre} ({len(items)}) :")
                lignes.extend(f"  • {it}" for it in items)
        if not any(items for _, items in sections_agenda + sections_fin[:1]):
            lignes.append("Rien de nouveau : l'agenda est à jour.")
        return "\n".join(lignes)


def sync(
    devoirs: list[Devoir],
    cal: CalendarBackend,
    now: datetime,
    dry_run: bool = False,
    progress: Progress | None = None,
) -> SyncReport:
    """Synchronise les devoirs vers `cal`. Ne lève pas : les erreurs d'écriture
    sont listées dans `erreurs`. En `dry_run`, aucune écriture n'est faite."""
    etape = progress or (lambda _msg: None)
    rapport = SyncReport(dry_run=dry_run)
    maintenant = _as_paris(now).timestamp()
    etape("Apple : lecture du calendrier…")
    existants = cal.list_events()
    uids_actifs: set[str] = set()

    for i, d in enumerate(devoirs, 1):
        etape(f"Apple : comparaison et écriture des événements ({i}/{len(devoirs)})…")
        libelle = f"{d.nom} — {d.matiere}"
        if not d.due:
            rapport.sans_date.append(libelle)
            continue

        spec = build_event_spec(d)
        uids_actifs.add(spec.uid)

        en_retard = not d.rendu and d.due < maintenant
        if en_retard:
            possible = (not d.cutoff) or d.cutoff > maintenant
            suffixe = "(dépôt encore possible)" if possible else "(fermé)"
            rapport.en_retard.append(f"{libelle} — échéance {_fmt_echeance(d.due)} {suffixe}")
            if spec.uid not in existants:
                continue

        info = existants.get(spec.uid)
        if info is None:
            if not dry_run:
                try:
                    cal.put_event(spec)
                except Exception as e:
                    log.exception("Synchro : écriture impossible pour %s", libelle)
                    rapport.erreurs.append(f"Écriture impossible pour « {libelle} » : {e}")
                    continue
            rapport.ajoutes.append(f"{spec.summary} ({_fmt_echeance(d.due)})")
            continue

        if info.hash == spec.hash:
            continue

        deplace = info.end is not None and _as_paris(info.end).timestamp() != spec.end.timestamp()
        if not dry_run:
            try:
                cal.put_event(spec)
            except Exception as e:
                log.exception("Synchro : mise à jour impossible pour %s", libelle)
                rapport.erreurs.append(f"Mise à jour impossible pour « {libelle} » : {e}")
                continue
        if deplace:
            rapport.deplaces.append(f"{spec.summary} ({_fmt_echeance(d.due)})")
        elif d.rendu:
            rapport.rendus.append(spec.summary)
        else:
            rapport.mis_a_jour.append(f"{spec.summary} ({_fmt_echeance(d.due)})")

    obsoletes = [u for u in existants if u.startswith(UID_PREFIX) and u not in uids_actifs]
    for uid in obsoletes:
        etape("Apple : suppression des devoirs retirés de Moodle…")
        if not dry_run:
            try:
                cal.delete_event(uid)
            except Exception as e:
                log.exception("Synchro : suppression impossible pour %s", uid)
                rapport.erreurs.append(f"Suppression impossible pour {uid} : {e}")
                continue
        rapport.supprimes.append(uid)

    return rapport


def _moodle_settings(cfg) -> tuple[str, str]:
    """(base_url, nom du calendrier) avec repli si la config Moodle est absente."""
    moodle = getattr(cfg, "moodle", None)
    base_url = getattr(moodle, "base_url", "") or DEFAULT_BASE_URL
    nom_cal = getattr(moodle, "calendar_name", "") or DEFAULT_CALENDAR
    return base_url, nom_cal


def load_devoirs(cfg, progress: Progress | None = None) -> list[Devoir]:
    """Lit les devoirs sur Moodle (lecture seule). Lève MoodleError / MoodleTokenError."""
    base_url, _ = _moodle_settings(cfg)
    token = load_token()
    with MoodleClient(base_url, token) as client:
        return client.fetch_devoirs(progress)


def _rapport_secours(devoirs: list[Devoir], maintenant: datetime, dry_run: bool, message: str) -> SyncReport:
    """Rapport sans iCloud : échéances en retard et sans date, plus le message d'erreur."""
    rapport = sync(devoirs, NullCalendar(), maintenant, dry_run=True)
    rapport.ajoutes.clear()     # état réel de l'agenda inconnu : pas de liste d'ajouts
    rapport.dry_run = dry_run
    rapport.erreurs.append(message)
    return rapport


def _libelle_document(d: dict) -> str:
    """Ligne affichée pour un document à récupérer (préfixe selon le statut)."""
    prefixe = {"MAJ_DISPO": "mis à jour : ", "MANQUANT_LOCAL": "absent en local : "}.get(d["statut"], "")
    dossier = d.get("dest") or "(racine de la matière)"
    return f"{prefixe}« {d['nom']} » — {d['matiere']} → {dossier}"


def ajouter_documents(rapport: SyncReport, cfg) -> SyncReport:
    """Renseigne les documents Moodle à récupérer (lecture seule, non bloquant).

    Une erreur devient un avertissement : elle ne bloque ni la synchro ni `last_sync`."""
    try:
        documents = documents_a_recuperer(cfg, progression=None)
    except Exception as e:
        log.warning("Synchro : recherche de nouveaux documents impossible : %s", e)
        rapport.avertissements.append(f"Recherche de nouveaux documents impossible : {e}")
        return rapport
    rapport.nouveaux_documents = [_libelle_document(d) for d in documents]
    rapport.cles_documents = [d["cle"] for d in documents]
    return rapport


def run_sync(cfg, dry_run: bool = False, progress: Progress | None = None) -> SyncReport:
    """Point d'entrée de l'interface : Moodle → iCloud. Ne lève jamais.

    `progress` reçoit le libellé de chaque étape (Moodle…, Apple…)."""
    etape = progress or (lambda _msg: None)
    _, nom_cal = _moodle_settings(cfg)
    maintenant = datetime.now(PARIS)

    try:
        devoirs = load_devoirs(cfg, etape)
    except MoodleTokenError as e:
        return SyncReport(dry_run=dry_run, erreurs=[str(e)])
    except MoodleError as e:
        return SyncReport(dry_run=dry_run, erreurs=[str(e)])
    except Exception as e:
        log.exception("Synchro : lecture Moodle impossible")
        return SyncReport(dry_run=dry_run, erreurs=[f"Lecture Moodle impossible : {e}"])

    # Documents : calculés une fois, ajoutés quel que soit le chemin iCloud (y compris secours).
    etape("Moodle : recherche de nouveaux documents…")
    documents = ajouter_documents(SyncReport(dry_run=dry_run), cfg)
    rapport = _synchro_icloud(devoirs, maintenant, dry_run, etape, nom_cal)
    rapport.nouveaux_documents = documents.nouveaux_documents
    rapport.cles_documents = documents.cles_documents
    rapport.avertissements = documents.avertissements + rapport.avertissements
    return rapport


def _synchro_icloud(devoirs: list[Devoir], maintenant: datetime, dry_run: bool,
                    etape: Progress, nom_cal: str) -> SyncReport:
    """Partie iCloud de la synchro, après la lecture Moodle. Ne lève jamais."""
    etape("Apple : lecture des identifiants (trousseau Windows)…")
    try:
        from .secrets import get_credentials
        identifiants = get_credentials()
    except Exception as e:
        log.exception("Synchro : trousseau inaccessible")
        return _rapport_secours(devoirs, maintenant, dry_run, f"Trousseau inaccessible : {e}")

    if identifiants is None:
        return _rapport_secours(
            devoirs, maintenant, dry_run, "iCloud non configuré : Moodle → Configurer iCloud…"
        )
    apple_id, mot_de_passe = identifiants

    try:
        from .icloud import ICloudAuthError, ICloudCalendar, ICloudError
    except ImportError as e:
        return _rapport_secours(devoirs, maintenant, dry_run, f"Module iCloud indisponible : {e}")

    try:
        etape("Apple : connexion à iCloud…")
        cal = ICloudCalendar(apple_id, mot_de_passe, nom_cal)
        if hasattr(cal, "connect"):
            cal.connect()
        return sync(devoirs, cal, maintenant, dry_run=dry_run, progress=etape)
    except ICloudAuthError:
        log.warning("Synchro : identifiants iCloud refusés")
        message = "Identifiants iCloud refusés : vérifier l'Apple ID / le mot de passe d'application"
    except ICloudError as e:
        log.warning("Synchro : erreur iCloud : %s", e)
        message = f"Erreur iCloud : {e}"
    except Exception as e:
        log.exception("Synchro : erreur iCloud inattendue")
        message = f"Erreur iCloud inattendue : {e}"
    return _rapport_secours(devoirs, maintenant, dry_run, message)
