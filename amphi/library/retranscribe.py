"""Retranscrire un cours déjà livré (ex. importé depuis OneDrive) avec le moteur actuel.

Le worker ne sait transcrire que depuis un dossier de travail `.staging\\...`. `prepare` en
crée un pour le cours : il ne contient qu'un fichier `retranscribe.json` qui pointe vers
l'archive audio du cours (aucune copie, aucun décodage préalable). Le worker lit alors cet
audio directement, passe par la même chaîne qu'un cours neuf (normalisation de la parole en
WAV temporaire, puis Whisper), puis :

- remplace `transcript.md` ;
- supprime l'ancien `resume.md`, que Gemini refait, puis met à jour l'index des thèmes
  (les anciens passages du cours sont écartés) ;
- ne réécrit JAMAIS l'archive audio.

`paused=True` laisse la ligne « en pause » dans la file d'attente : la transcription part au
clic sur « ▶ Reprendre ».

    python -m amphi.library.retranscribe                 # liste les cours retranscriptibles
    python -m amphi.library.retranscribe 8 --pause       # prépare le cours n° 8, en pause
    python -m amphi.library.retranscribe 8               # prépare et met en file (démarre)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..models import Status
from ..paths import STAGING_DIR, ensure_dirs
from .store import Recording, Store

RETRANSCRIBE_MARK = "retranscribe.json"  # lu par transcribe/worker.py
ELIGIBLE = (Status.DONE, Status.SUMMARY_FAILED, Status.INDEX_FAILED)
_AUDIO_NAMES = ("audio.flac", "audio.wav", "audio.opus", "audio.ogg", "audio.m4a")
# fichiers intermédiaires d'une tentative précédente, retirés avant de repartir de zéro
_STALE = ("segments.jsonl", "transcript.md", "norm.wav", "resume.wav")


def find_audio(rec: Recording) -> Path | None:
    """Archive audio du cours : celle de la base si elle existe, sinon audio.* du dossier."""
    if rec.audio_path and Path(rec.audio_path).is_file():
        return Path(rec.audio_path)
    if rec.target_dir:
        for name in _AUDIO_NAMES:
            p = Path(rec.target_dir) / name
            if p.is_file():
                return p
    return None


def can_retranscribe(rec: Recording) -> bool:
    return rec.status in ELIGIBLE and bool(rec.target_dir) and find_audio(rec) is not None


def prepare(store: Store, rec_id: int, *, paused: bool = False) -> Path:
    """Prépare la retranscription du cours `rec_id`. Renvoie le dossier de travail.

    Lève ValueError si le cours est introuvable, pas dans un état retranscriptible, ou sans
    archive audio."""
    rec = store.get(rec_id)
    if rec is None:
        raise ValueError(f"cours n° {rec_id} introuvable")
    if rec.status not in ELIGIBLE:
        raise ValueError(f"« {rec.titre} » est « {rec.status_label} » : rien à retranscrire pour l'instant")
    audio = find_audio(rec)
    if audio is None:
        raise ValueError(f"pas d'archive audio pour « {rec.titre} » dans {rec.target_dir}")

    ensure_dirs()
    # `retrans_<id>` (et non `rec_*`) : le balayage des captures orphelines ne le touche jamais
    work = STAGING_DIR / f"retrans_{rec.id}"
    work.mkdir(parents=True, exist_ok=True)
    for name in _STALE:
        (work / name).unlink(missing_ok=True)
    (work / RETRANSCRIBE_MARK).write_text(
        json.dumps({"audio": str(audio)}, ensure_ascii=False), encoding="utf-8"
    )
    store.update(
        rec.id,
        status=Status.PAUSED_USER if paused else Status.QUEUED,
        staging_dir=str(work),
        progress=0.0,
        stage="retranscription depuis l'archive",
        checkpoint_s=0.0,
        error="",
    )
    return work


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("id", nargs="?", type=int, help="n° du cours (sans argument : liste)")
    ap.add_argument("--pause", action="store_true", help="laisser en pause (▶ Reprendre pour lancer)")
    args = ap.parse_args(argv)

    store = Store()
    try:
        if args.id is None:
            for r in store.all(limit=500):
                mark = "" if can_retranscribe(r) else "  (non retranscriptible)"
                print(f"{r.id:4d}  {r.date}  {r.matiere} — {r.titre}  [{r.status_label}]{mark}")
            return 0
        try:
            work = prepare(store, args.id, paused=args.pause)
        except ValueError as exc:
            print(f"Impossible : {exc}")
            return 1
        rec = store.get(args.id)
        etat = "en pause — clique ▶ Reprendre dans Amphi" if args.pause else "en file d'attente"
        print(f"Prêt : {rec.matiere} — {rec.titre} ({rec.date}), {etat}.")
        print(f"Dossier de travail : {work}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
