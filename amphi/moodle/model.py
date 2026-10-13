"""Structures partagées entre le client Moodle, la synchro et le calendrier iCloud."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

UID_PREFIX = "amphi-moodle-assign-"
UID_DOMAIN = "@moodle.ipsa.fr"


def uid_for(assign_id: int) -> str:
    return f"{UID_PREFIX}{assign_id}{UID_DOMAIN}"


@dataclass
class Devoir:
    """Un devoir Moodle (mod_assign) d'une section du campus Paris."""

    assign_id: int
    cmid: int
    course_id: int
    matiere: str            # = nom du dossier de la matière dans B3
    nom: str                # nom du devoir sur Moodle (« TP4 »)
    due: int                # échéance, epoch s ; 0 = pas de date
    cutoff: int             # date limite stricte, epoch s ; 0 = aucune
    intro: str              # consignes, texte brut (HTML retiré)
    statut: str             # « submitted », « new », « draft »…
    rendu_le: int           # epoch s du dernier dépôt ; 0 si rien
    fichiers: list[str] = field(default_factory=list)
    url: str = ""           # page du devoir : <base>/mod/assign/view.php?id=<cmid>

    @property
    def rendu(self) -> bool:
        return self.statut == "submitted"

    @property
    def uid(self) -> str:
        return uid_for(self.assign_id)


@dataclass
class EventSpec:
    """Événement à écrire dans le calendrier (calculé par la synchro)."""

    uid: str
    summary: str
    description: str
    url: str
    start: datetime                     # aware (fuseau Europe/Paris)
    end: datetime                       # aware ; = échéance
    alarms: list[timedelta] = field(default_factory=list)   # délais AVANT l'échéance (end), positifs
    hash: str = ""                      # empreinte du contenu, stockée en X-AMPHI-HASH


@dataclass
class EventInfo:
    """Événement Amphi déjà présent dans le calendrier."""

    uid: str
    hash: str                           # X-AMPHI-HASH lu ; "" si absent
    end: datetime | None                # échéance actuellement dans l'agenda
