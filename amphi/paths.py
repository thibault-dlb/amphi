"""Emplacements de fichiers et helpers de nommage.

Tout ce qui est volatil ou local (config, base, staging, logs) vit sous APP_DIR.
Les livrables finaux vont dans OneDrive\\Cours (voir config.courses_root).
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

# .../Amphi/amphi/paths.py -> APP_DIR = .../Amphi
APP_DIR = Path(__file__).resolve().parent.parent

CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "amphi.db"
STAGING_DIR = APP_DIR / ".staging"
LOG_DIR = APP_DIR / "logs"
ASSETS_DIR = APP_DIR / "amphi" / "assets"
WORKER_LOCK = APP_DIR / ".worker.lock"
WORKER_STOP_FLAG = APP_DIR / ".worker.stop"
RECORDING_FLAG = APP_DIR / ".recording"   # présent = enregistrement en cours, worker au repos
FORCE_BATTERY_FLAG = APP_DIR / ".force_battery"  # présent = transcrire même sur batterie

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")


def ensure_dirs() -> None:
    for d in (STAGING_DIR, LOG_DIR, ASSETS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def sanitize_component(text: str, *, fallback: str = "Sans titre") -> str:
    """Rends une chaîne utilisable comme nom de dossier/fichier Windows.

    Les accents sont conservés (NTFS gère l'UTF-16 et les dossiers existants de
    l'utilisateur en contiennent) ; seuls les caractères interdits sont retirés.
    """
    text = unicodedata.normalize("NFC", text).strip()
    text = _FORBIDDEN.sub("", text)
    text = _WS.sub(" ", text).strip(" .")
    text = text[:120].strip(" .")
    return text or fallback


def unique_dir(parent: Path, name: str) -> Path:
    """Renvoie parent/name, en suffixant ' (2)', ' (3)'… si déjà pris."""
    candidate = parent / name
    i = 2
    while candidate.exists():
        candidate = parent / f"{name} ({i})"
        i += 1
    return candidate


def find_onedrive() -> Path | None:
    for var in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        p = os.environ.get(var)
        if p and Path(p).is_dir():
            return Path(p)
    guess = Path.home() / "OneDrive"
    return guess if guess.is_dir() else None


def default_courses_root() -> Path:
    """Meilleure hypothèse pour la racine des cours au tout premier lancement."""
    od = find_onedrive()
    if od:
        for name in ("Cours", "Courses", "cours"):
            if (od / name).is_dir():
                return od / name
        return od / "Cours"
    return Path.home() / "Cours"
