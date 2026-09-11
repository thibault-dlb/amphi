"""Types partagés entre l'UI, le stockage et le worker de transcription."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field


class Status:
    """Cycle de vie d'un enregistrement (colonne `status` en base)."""

    RECORDING = "recording"          # capture en cours (jamais persisté en base)
    QUEUED = "queued"                # validé, en attente de transcription
    TRANSCRIBING = "transcribing"    # transcription en cours
    PAUSED_NO_AC = "paused_no_ac"    # en pause : laptop sur batterie
    PAUSED_USER = "paused_user"      # en pause : demandé par l'utilisateur
    TRANSCRIBED = "transcribed"      # transcript.md écrit, avant résumé
    SUMMARIZING = "summarizing"      # appel Gemini en cours (résumé)
    INDEXING = "indexing"            # appel Gemini en cours (index des thèmes de la matière)
    DONE = "done"                    # transcript (+ résumé, + index) livrés
    TRANSCRIBE_FAILED = "transcribe_failed"
    SUMMARY_FAILED = "summary_failed"  # transcript OK, résumé en échec
    INDEX_FAILED = "index_failed"      # transcript + résumé OK, index des thèmes en échec

    # PAUSED_USER volontairement hors ACTIONABLE : le worker ne doit pas le
    # reprendre tout seul — il attend un clic « Reprendre ».
    ACTIONABLE = {QUEUED, TRANSCRIBING, PAUSED_NO_AC, TRANSCRIBED, SUMMARIZING, INDEXING}
    TERMINAL = {DONE, TRANSCRIBE_FAILED}
    # Étapes où l'utilisateur peut mettre la transcription en pause.
    PAUSABLE = {QUEUED, TRANSCRIBING, PAUSED_NO_AC}

    LABELS_FR = {
        RECORDING: "enregistrement",
        QUEUED: "en attente",
        TRANSCRIBING: "transcription",
        PAUSED_NO_AC: "en pause (batterie)",
        PAUSED_USER: "en pause",
        TRANSCRIBED: "transcript prêt",
        SUMMARIZING: "résumé…",
        INDEXING: "index des thèmes…",
        DONE: "terminé",
        TRANSCRIBE_FAILED: "échec transcription",
        SUMMARY_FAILED: "résumé échoué",
        INDEX_FAILED: "index échoué",
    }


@dataclass
class Marker:
    """Repère posé pendant le cours, en secondes sur la timeline écrite (hors pauses)."""

    offset_s: float
    label: str = "important"
    note: str = ""


@dataclass
class RecordingMeta:
    """Métadonnées saisies à la validation + calculées à la capture."""

    titre: str
    matiere: str
    niveau: str
    date: str                       # AAAA-MM-JJ
    langue: str = "fr"              # "fr" | "en"
    duree_s: float = 0.0
    sample_rate: int = 16000
    markers: list[Marker] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    app_version: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "RecordingMeta":
        d = dict(d)
        d["markers"] = [Marker(**m) for m in d.get("markers", [])]
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    @staticmethod
    def hhmmss(seconds: float) -> str:
        seconds = max(0, int(round(seconds)))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
