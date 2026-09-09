"""Index SQLite partagé entre l'UI et le worker (processus distincts, mode WAL)."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import Marker, RecordingMeta, Status
from ..paths import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    status         TEXT NOT NULL,

    titre          TEXT NOT NULL,
    matiere        TEXT NOT NULL,
    niveau         TEXT NOT NULL,
    date           TEXT NOT NULL,
    langue         TEXT NOT NULL DEFAULT 'fr',
    duree_s        REAL NOT NULL DEFAULT 0,
    sample_rate    INTEGER NOT NULL DEFAULT 16000,
    markers_json   TEXT NOT NULL DEFAULT '[]',

    staging_dir    TEXT,
    target_dir     TEXT,
    audio_path     TEXT,
    transcript_path TEXT,
    resume_path    TEXT,

    progress       REAL NOT NULL DEFAULT 0,
    stage          TEXT NOT NULL DEFAULT '',
    checkpoint_s   REAL NOT NULL DEFAULT 0,
    error          TEXT NOT NULL DEFAULT '',
    engine_json    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_recordings_status ON recordings(status);
CREATE INDEX IF NOT EXISTS ix_recordings_created ON recordings(created_at);
"""

# Ordre de service du worker : les transcriptions passent avant les résumés
# Gemini. Un résumé (TRANSCRIBED / SUMMARIZING) est un appel réseau qui ne doit
# pas retarder la transcription suivante — il attend que la file de transcription
# soit vide.
_JOB_PRIORITY = {
    Status.TRANSCRIBING: 0,   # reprise d'une transcription entamée
    Status.PAUSED_NO_AC: 1,   # transcription en pause (batterie) à reprendre
    Status.QUEUED: 2,         # nouvelle transcription
    Status.SUMMARIZING: 3,    # reprise d'un résumé entamé
    Status.TRANSCRIBED: 4,    # transcript livré, résumé à faire
}


@dataclass
class Recording:
    id: int
    created_at: float
    updated_at: float
    status: str
    titre: str
    matiere: str
    niveau: str
    date: str
    langue: str
    duree_s: float
    sample_rate: int
    markers_json: str
    staging_dir: str | None
    target_dir: str | None
    audio_path: str | None
    transcript_path: str | None
    resume_path: str | None
    progress: float
    stage: str
    checkpoint_s: float
    error: str
    engine_json: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Recording":
        return cls(**{k: row[k] for k in row.keys()})

    @property
    def markers(self) -> list[Marker]:
        return [Marker(**m) for m in json.loads(self.markers_json or "[]")]

    @property
    def engine(self) -> dict[str, Any]:
        return json.loads(self.engine_json or "{}")

    @property
    def status_label(self) -> str:
        return Status.LABELS_FR.get(self.status, self.status)

    def meta(self) -> RecordingMeta:
        return RecordingMeta(
            titre=self.titre,
            matiere=self.matiere,
            niveau=self.niveau,
            date=self.date,
            langue=self.langue,
            duree_s=self.duree_s,
            sample_rate=self.sample_rate,
            markers=self.markers,
            created_at=self.created_at,
        )


class Store:
    def __init__(self, db_path: Path | None = None) -> None:
        self.path = db_path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.path, timeout=30.0)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA busy_timeout=30000")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def close(self) -> None:
        try:
            self._con.close()
        except sqlite3.Error:
            pass

    # -- écriture ---------------------------------------------------------
    def add(
        self,
        meta: RecordingMeta,
        *,
        staging_dir: Path,
        target_dir: Path,
        engine: dict[str, Any],
        status: str = Status.QUEUED,
    ) -> int:
        now = time.time()
        markers = json.dumps(
            [{"offset_s": m.offset_s, "label": m.label, "note": m.note} for m in meta.markers],
            ensure_ascii=False,
        )
        cur = self._con.execute(
            """INSERT INTO recordings
               (created_at, updated_at, status, titre, matiere, niveau, date, langue,
                duree_s, sample_rate, markers_json, staging_dir, target_dir, engine_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now, now, status, meta.titre, meta.matiere, meta.niveau, meta.date,
                meta.langue, meta.duree_s, meta.sample_rate, markers,
                str(staging_dir), str(target_dir), json.dumps(engine, ensure_ascii=False),
            ),
        )
        self._con.commit()
        return int(cur.lastrowid)

    def update(self, rec_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._con.execute(
            f"UPDATE recordings SET {cols} WHERE id=?", (*fields.values(), rec_id)
        )
        self._con.commit()

    def set_status(self, rec_id: int, status: str, *, error: str = "") -> None:
        self.update(rec_id, status=status, error=error)

    def set_progress(
        self, rec_id: int, *, progress: float, stage: str, checkpoint_s: float | None = None
    ) -> None:
        fields: dict[str, Any] = {"progress": max(0.0, min(1.0, progress)), "stage": stage}
        if checkpoint_s is not None:
            fields["checkpoint_s"] = checkpoint_s
        self.update(rec_id, **fields)

    def delete(self, rec_id: int) -> None:
        self._con.execute("DELETE FROM recordings WHERE id=?", (rec_id,))
        self._con.commit()

    # -- lecture --------------------------------------------------------
    def get(self, rec_id: int) -> Recording | None:
        row = self._con.execute("SELECT * FROM recordings WHERE id=?", (rec_id,)).fetchone()
        return Recording.from_row(row) if row else None

    def all(self, limit: int = 300) -> list[Recording]:
        rows = self._con.execute(
            "SELECT * FROM recordings ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [Recording.from_row(r) for r in rows]

    def active(self) -> list[Recording]:
        marks = ",".join("?" * len(Status.ACTIONABLE))
        rows = self._con.execute(
            f"SELECT * FROM recordings WHERE status IN ({marks}) ORDER BY created_at",
            tuple(Status.ACTIONABLE),
        ).fetchall()
        return [Recording.from_row(r) for r in rows]

    def pending_exists(self) -> bool:
        return bool(self.active())

    def next_job(self) -> Recording | None:
        """Prochain enregistrement à traiter par le worker (None si file vide)."""
        jobs = self.active()
        if not jobs:
            return None
        jobs.sort(key=lambda r: (_JOB_PRIORITY.get(r.status, 9), r.created_at))
        return jobs[0]
