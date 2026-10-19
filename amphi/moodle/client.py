"""Client minimal de l'API web-services Moodle, en LECTURE SEULE.

Seules des fonctions de lecture sont appelées (site_info, cours, contenus,
devoirs, statut de dépôt). Aucune fonction d'écriture n'est utilisée.
Le token n'est jamais journalisé.
"""

from __future__ import annotations

import html
import logging
import re
import time
from typing import Any, Callable

import httpx

from ..paths import APP_DIR, sanitize_component
from .model import Devoir

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)   # une ligne par appel Moodle : inutile

TOKEN_PATH = APP_DIR / ".moodle_token"
RETRIES = 5                 # tentatives supplémentaires sur erreur réseau
RETRY_DELAY_S = 3.0

_MATIERE_PREFIX = re.compile(r"^\d{2}-[A-Za-z0-9]+-\s*")
_CODE_COURS = re.compile(r"^\d{2}-([A-Za-z0-9]+)-")
# Code du cours (« 26-PBI-… ») → dossier local, quand le nom Moodle ne correspond pas
MATIERES_FORCEES = {
    "PBI": "Projet Bachelor IPSA",
}
# Campus à exclure (noms de section) ; tout ce qui n'en mentionne aucun est gardé pour les devoirs
AUTRES_CAMPUS = ("TOULOUSE",)
_YEAR_PREFIX = re.compile(r"^(\d{2})-")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_CLOSE_BLOCK = re.compile(r"</(?:p|li)\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_MANY_NL = re.compile(r"\n{3,}")


class MoodleError(Exception):
    """Erreur générale de l'API Moodle ou de la connexion."""


class MoodleTokenError(MoodleError):
    """Token Moodle absent, expiré ou invalide."""


def load_token() -> str:
    """Lit le token depuis APP_DIR/.moodle_token."""
    try:
        token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = ""
    if not token:
        raise MoodleTokenError("Token Moodle introuvable (.moodle_token)")
    return token


def matiere_from_fullname(fullname: str) -> str:
    """« 26-BIN313-Apprentissage automatique : Machine learning »
    → « Apprentissage automatique - Machine learning » (nom de dossier B3)."""
    texte = html.unescape(fullname or "")
    code = _CODE_COURS.match(texte)
    if code and code.group(1).upper() in MATIERES_FORCEES:
        return MATIERES_FORCEES[code.group(1).upper()]
    texte = _MATIERE_PREFIX.sub("", texte, count=1)
    texte = texte.replace(":", "-")
    texte = sanitize_component(texte)
    return texte.rstrip(" -").strip()


def html_to_text(s: str) -> str:
    """Convertit le HTML Moodle (consignes) en texte brut lisible."""
    texte = _BR.sub("\n", s or "")
    texte = _CLOSE_BLOCK.sub("\n", texte)
    texte = _TAG.sub("", texte)
    texte = html.unescape(texte)
    texte = _MANY_NL.sub("\n\n", texte)
    return texte.strip()


def _cmids_autorises(sections: list[dict[str, Any]]) -> set[int]:
    """Modules autorisés d'un cours : sections « PARIS » + section 0 si le cours
    en contient ; sinon toutes les sections (cours sans découpage par campus)."""
    numeros_paris = {
        s.get("section") for s in sections if "PARIS" in (s.get("name") or "").upper()
    }
    if not numeros_paris:
        return {m["id"] for s in sections for m in s.get("modules", []) or []}
    autorises: set[int] = set()
    for s in sections:
        if s.get("section") == 0 or s.get("section") in numeros_paris:
            autorises |= {m["id"] for m in s.get("modules", []) or []}
    return autorises


def _cmids_devoirs(sections: list[dict[str, Any]]) -> set[int]:
    """Modules retenus pour les devoirs : tout le cours, sauf les sections d'un autre
    campus (nom contenant « TOULOUSE », ou placées sous un en-tête « CAMPUS TOULOUSE »
    jusqu'au prochain en-tête « CAMPUS … »). Les sections sans campus sont gardées."""
    retenus: set[int] = set()
    sous_autre_campus = False
    for s in sections:
        nom = (s.get("name") or "").upper()
        if "CAMPUS" in nom:
            sous_autre_campus = "PARIS" not in nom
        autre = any(c in nom for c in AUTRES_CAMPUS) or (sous_autre_campus and "PARIS" not in nom)
        if not autre:
            retenus |= {m["id"] for m in s.get("modules", []) or []}
    return retenus


def _statut_devoir(reponse: dict[str, Any]) -> tuple[str, int, list[str]]:
    """Renvoie (statut, rendu_le, fichiers) à partir de mod_assign_get_submission_status."""
    dernier = reponse.get("lastattempt") or {}
    sub = dernier.get("submission") or {}
    equipe = dernier.get("teamsubmission") or {}
    statut = sub.get("status") or equipe.get("status") or "new"
    source = sub if sub else equipe
    rendu_le = int(source.get("timemodified") or 0) if statut == "submitted" else 0
    fichiers: list[str] = []
    for plugin in source.get("plugins") or []:
        for zone in plugin.get("fileareas") or []:
            for fichier in zone.get("files") or []:
                nom = fichier.get("filename")
                if nom and nom not in fichiers:
                    fichiers.append(nom)
    return statut, rendu_le, fichiers


class MoodleClient:
    """Client HTTP (httpx) pour l'API REST Moodle, lecture seule."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MoodleClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call(self, fn: str, **params: Any) -> Any:
        """Appelle une fonction web-service Moodle et renvoie le JSON décodé.

        Les listes sont envoyées sous la forme name[0]=..., name[1]=...
        """
        data: dict[str, Any] = {
            "wstoken": self._token,
            "wsfunction": fn,
            "moodlewsrestformat": "json",
        }
        for nom, valeur in params.items():
            if isinstance(valeur, (list, tuple)):
                for i, item in enumerate(valeur):
                    data[f"{nom}[{i}]"] = item
            else:
                data[nom] = valeur

        url = f"{self.base_url}/webservice/rest/server.php"
        derniere_erreur: Exception | None = None
        for tentative in range(RETRIES + 1):
            try:
                reponse = self._http.post(url, data=data)
                break
            except httpx.TransportError as e:
                derniere_erreur = e
                log.warning("Moodle : erreur réseau sur %s (tentative %d)", fn, tentative + 1)
                if tentative < RETRIES:
                    time.sleep(RETRY_DELAY_S)
        else:
            raise MoodleError(f"Moodle injoignable : {derniere_erreur}")

        if reponse.status_code >= 400:
            raise MoodleError(f"Moodle a répondu HTTP {reponse.status_code} pour {fn}")
        try:
            payload = reponse.json()
        except ValueError as e:
            raise MoodleError(f"Réponse Moodle non JSON pour {fn}") from e

        if isinstance(payload, dict) and "exception" in payload:
            code = payload.get("errorcode", "")
            message = payload.get("message") or "erreur inconnue"
            log.warning("Moodle : %s a renvoyé %s (%s)", fn, code, message)
            if code == "invalidtoken":
                raise MoodleTokenError(
                    "Le token Moodle a expiré ou n'est plus valide : régénérez-le "
                    "(launch.php) puis remplacez le fichier .moodle_token."
                )
            raise MoodleError(message)
        return payload

    def fetch_devoirs(self, progress: Callable[[str], None] | None = None) -> list[Devoir]:
        """Liste les devoirs (mod_assign) de l'année courante, hors autres campus.

        `progress` reçoit un libellé à chaque étape (affichage dans l'interface)."""
        etape = progress or (lambda _msg: None)
        etape("Moodle : connexion…")
        site = self.call("core_webservice_get_site_info")
        userid = site["userid"]
        etape("Moodle : liste des cours…")
        cours = self.call("core_enrol_get_users_courses", userid=userid)

        # Cours de l'année courante : préfixe « NN- » ; on ne garde que le NN maximal.
        annees = {}
        for c in cours:
            m = _YEAR_PREFIX.match(c.get("fullname") or "")
            if m:
                annees[c["id"]] = int(m.group(1))
        if not annees:
            log.info("Moodle : aucun cours de l'année courante trouvé")
            return []
        annee_max = max(annees.values())
        retenus = [c for c in cours if annees.get(c["id"]) == annee_max]
        ids_cours = [c["id"] for c in retenus]
        noms_cours = {c["id"]: c.get("fullname") or "" for c in retenus}

        autorises: dict[int, set[int]] = {}
        for i, cid in enumerate(ids_cours, 1):
            etape(f"Moodle : sections des cours ({i}/{len(ids_cours)})…")
            contenu = self.call("core_course_get_contents", courseid=cid)
            autorises[cid] = _cmids_devoirs(contenu)

        etape("Moodle : liste des devoirs…")
        reponse = self.call("mod_assign_get_assignments", courseids=ids_cours)
        a_traiter = [
            (bloc["id"], a)
            for bloc in reponse.get("courses", [])
            for a in bloc.get("assignments", [])
            if a["cmid"] in autorises.get(bloc["id"], set())
        ]
        devoirs: list[Devoir] = []
        for i, (cid, a) in enumerate(a_traiter, 1):
            etape(f"Moodle : état des dépôts ({i}/{len(a_traiter)})…")
            statut_rep = self.call("mod_assign_get_submission_status", assignid=a["id"])
            statut, rendu_le, fichiers = _statut_devoir(statut_rep)
            devoirs.append(
                Devoir(
                    assign_id=a["id"],
                    cmid=a["cmid"],
                    course_id=cid,
                    matiere=matiere_from_fullname(noms_cours.get(cid, "")),
                    nom=a.get("name") or "",
                    due=a.get("duedate") or 0,
                    cutoff=a.get("cutoffdate") or 0,
                    intro=html_to_text(a.get("intro") or ""),
                    statut=statut,
                    rendu_le=rendu_le,
                    fichiers=fichiers,
                    url=f"{self.base_url}/mod/assign/view.php?id={a['cmid']}",
                )
            )

        log.info("Moodle : %d cours retenus, %d devoirs (hors autres campus)", len(retenus), len(devoirs))
        return devoirs
