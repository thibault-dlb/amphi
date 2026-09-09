"""Worker de transcription — processus autonome, découplé de l'UI.

Lancé par l'app (`python -m amphi.transcribe.worker`) quand la file n'est pas vide.
Tourne en priorité basse, ne transcrit que sur secteur (si cfg.ac_only), écrit un
checkpoint après chaque segment (`segments.jsonl`) et reprend là où il s'était arrêté.
Sort de lui-même après quelques minutes d'inactivité ; l'app le relance au besoin.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import logging
from dataclasses import asdict
from pathlib import Path

import psutil

from ..audio import encode
from ..config import Config
from ..library.store import Recording, Store
from ..logging_setup import setup
from ..models import RecordingMeta, Status
from ..paths import FORCE_BATTERY_FLAG, RECORDING_FLAG, WORKER_LOCK, WORKER_STOP_FLAG
from ..power import on_ac_power
from . import summarize as summ
from .engine import Segment, TranscriptionInterrupted, WhisperEngine
from .markdown import build_transcript_md

log = logging.getLogger(__name__)

IDLE_EXIT_S = 180.0
BATTERY_POLL_S = 20.0


# --------------------------------------------------------------------------- #
#  Verrou d'instance                                                          #
# --------------------------------------------------------------------------- #
def _acquire_lock() -> bool:
    """Un seul worker à la fois. Création atomique (O_EXCL) pour éviter la course
    entre deux workers lancés dans la même fenêtre de 15 s par l'app."""
    me = str(os.getpid()).encode()
    for _ in range(2):
        try:
            fd = os.open(WORKER_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, me)
            os.close(fd)
            return True
        except FileExistsError:
            try:
                pid = int(WORKER_LOCK.read_text().strip() or "-1")
            except (ValueError, OSError):
                pid = -1
            if pid == os.getpid():
                return True
            if pid > 0 and psutil.pid_exists(pid):
                return False
            # verrou périmé (process mort) : on le retire et on retente
            try:
                WORKER_LOCK.unlink()
            except OSError:
                return False
        except OSError:
            return False
    return False


def _touch_lock() -> None:
    try:
        os.utime(WORKER_LOCK, None)
    except OSError:
        pass


def _release_lock() -> None:
    try:
        if WORKER_LOCK.exists() and WORKER_LOCK.read_text().strip() == str(os.getpid()):
            WORKER_LOCK.unlink()
    except OSError:
        pass


def _lower_priority() -> None:
    try:
        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        try:
            p.ionice(psutil.IOPRIO_LOW)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        log.debug("Impossible d'abaisser la priorité", exc_info=True)


# --------------------------------------------------------------------------- #
#  Checkpoint JSONL                                                           #
# --------------------------------------------------------------------------- #
def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            break  # ligne tronquée (crash) : on s'arrête là, le reste sera refait
    return out


def _append_jsonl(path: Path, obj: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# --------------------------------------------------------------------------- #
#  Étapes                                                                     #
# --------------------------------------------------------------------------- #
def _make_engine(cfg: Config) -> WhisperEngine:
    e = cfg.engine
    return WhisperEngine(
        model_name=e.model,
        compute_type=e.compute_type,
        cpu_threads=e.cpu_threads,
        beam_size=e.beam_size,
        vad_filter=e.vad_filter,
    )


def _on_battery_blocked(cfg: Config) -> bool:
    """Sur batterie sans dérogation explicite de l'utilisateur (drapeau .force_battery)."""
    return cfg.ac_only and not on_ac_power() and not FORCE_BATTERY_FLAG.exists()


def _recording_blocks(cfg: Config) -> bool:
    return RECORDING_FLAG.exists() and not cfg.transcribe_while_recording


def _should_stand_down(cfg: Config) -> bool:
    """Le worker doit s'effacer : arrêt demandé, sur batterie, ou (selon réglage)
    enregistrement en cours — la transcription vole du CPU à la capture audio."""
    if WORKER_STOP_FLAG.exists() or _recording_blocks(cfg):
        return True
    return _on_battery_blocked(cfg)


def _cancel_fn(cfg: Config):
    return lambda: _should_stand_down(cfg)


def _transcribe(job: Recording, cfg: Config, store: Store, engine: WhisperEngine, work: Path) -> None:
    store.set_status(job.id, Status.TRANSCRIBING)
    capture = work / "capture.wav"
    if not capture.exists():
        raise FileNotFoundError(f"capture.wav introuvable dans {work}")

    jsonl = work / "segments.jsonl"
    done = _read_jsonl(jsonl)
    checkpoint = float(done[-1]["end"]) if done else 0.0
    duration = job.duree_s or encode.probe_duration(capture) or 0.0

    src = capture
    resume_from = 0.0
    prompt = cfg.matiere_prompt(job.matiere)

    # Reprise seulement si le checkpoint est assez loin ET que le trim réussit ;
    # sinon on repart de zéro avec un jsonl vierge (pas de segments en double).
    if checkpoint > 15.0:
        trimmed = work / "resume.wav"
        if encode.trim_from(capture, trimmed, checkpoint):
            src = trimmed
            resume_from = checkpoint
            tail = " ".join(s["text"] for s in done[-3:])[-400:]
            prompt = f"{prompt} {tail}".strip()
            log.info("Reprise du job %d à %s", job.id, RecordingMeta.hhmmss(checkpoint))
        else:
            log.warning("Trim impossible — reprise depuis le début")

    if resume_from == 0.0 and done:
        jsonl.unlink(missing_ok=True)  # on recommence proprement

    # remonte la parole (locuteur lointain / micro peu sensible) avant Whisper —
    # sur un fichier temporaire, l'archive FLAC reste faite à partir de l'original
    if cfg.engine.normalize_speech:
        normed = work / "norm.wav"
        if encode.normalize_speech(src, normed):
            src = normed
            log.info("Parole normalisée pour la transcription (job %d)", job.id)

    seen_end = resume_from

    def _on_seg(seg: Segment) -> None:
        nonlocal seen_end
        record = {
            "start": round(seg.start + resume_from, 3),
            "end": round(seg.end + resume_from, 3),
            "text": seg.text,
        }
        _append_jsonl(jsonl, record)
        seen_end = record["end"]
        if duration > 0:
            store.set_progress(
                job.id,
                progress=seen_end / duration,
                stage=(
                    f"transcription {RecordingMeta.hhmmss(seen_end)}"
                    f" / {RecordingMeta.hhmmss(duration)}"
                ),
                checkpoint_s=seen_end,
            )

    seg_iter, _info = engine.transcribe(
        src,
        language=(job.langue or cfg.default_langue or "fr"),
        initial_prompt=prompt,
        on_segment=_on_seg,
        should_cancel=_cancel_fn(cfg),
    )
    for _ in seg_iter:  # la consommation déclenche _on_seg ; l'interruption remonte
        pass

    segments = [Segment(**d) for d in _read_jsonl(jsonl)]
    meta = job.meta()
    meta.duree_s = duration or seen_end
    engine_desc = f"faster-whisper {cfg.engine.model} ({cfg.engine.compute_type})"
    (work / "transcript.md").write_text(
        build_transcript_md(meta, segments, engine_desc=engine_desc), encoding="utf-8"
    )
    store.set_progress(job.id, progress=1.0, stage="transcript prêt", checkpoint_s=seen_end)
    log.info("Transcription terminée (job %d, %d segments)", job.id, len(segments))


def _finalize(job: Recording, cfg: Config, store: Store, work: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    audio_path = encode.archive_audio(work / "capture.wav", target, cfg.keep_audio_format)

    transcript_target = target / "transcript.md"
    shutil.copy2(work / "transcript.md", transcript_target)

    from .. import __version__

    meta = job.meta()
    meta.duree_s = job.duree_s or encode.probe_duration(work / "capture.wav") or meta.duree_s
    meta.app_version = __version__
    payload = asdict(meta)
    payload["engine"] = {
        "model": cfg.engine.model,
        "compute_type": cfg.engine.compute_type,
        "cpu_threads": cfg.engine.cpu_threads,
        "beam_size": cfg.engine.beam_size,
    }
    payload["finalized_at"] = time.time()
    (target / "meta.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    store.update(
        job.id,
        target_dir=str(target),
        audio_path=str(audio_path) if audio_path else "",
        transcript_path=str(transcript_target),
        status=Status.TRANSCRIBED,
    )
    log.info("Livré : %s", target)


def _maybe_summarize(job: Recording, cfg: Config, store: Store, target: Path) -> None:
    resume_md = target / "resume.md"
    if resume_md.exists():
        store.update(job.id, resume_path=str(resume_md), status=Status.DONE, stage="terminé")
        return

    if not (cfg.gemini.is_active() and cfg.gemini.auto_after_transcription):
        store.update(job.id, status=Status.DONE, stage="terminé")
        return

    transcript_text = (target / "transcript.md").read_text(encoding="utf-8")
    body = summ.strip_front_matter(transcript_text)
    # rien de transcrit (silence, test) -> inutile d'appeler Gemini
    if "aucune parole détectée" in body or len(body.strip()) < 150:
        store.update(job.id, status=Status.DONE, stage="terminé (rien à résumer)")
        return

    if _on_battery_blocked(cfg):
        raise TranscriptionInterrupted  # on attend le secteur pour le résumé aussi

    store.set_status(job.id, Status.SUMMARIZING)
    store.set_progress(job.id, progress=1.0, stage="résumé Gemini…")
    try:
        content = summ.summarize(transcript_text, job.meta(), cfg.gemini)
        resume_md.write_text(content, encoding="utf-8")
        store.update(job.id, resume_path=str(resume_md), status=Status.DONE, stage="terminé")
        log.info("Résumé écrit : %s", resume_md)
    except summ.SummarySkipped as exc:
        log.info("Résumé non disponible (%s) — transcript livré seul", exc)
        store.update(job.id, status=Status.DONE, stage="terminé (sans résumé)")
    except summ.SummaryError as exc:
        log.warning("Résumé en échec (job %d) : %s", job.id, exc)
        store.set_status(job.id, Status.SUMMARY_FAILED, error=str(exc)[:500])


def _remove_tree(path: Path, attempts: int = 6) -> bool:
    """rmtree tolérant aux verrous de fichiers transitoires (ffmpeg/PyAV qui relâchent)."""
    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if i == attempts - 1:
                log.warning("Nettoyage de %s impossible (verrou) — réessai plus tard", path)
                return False
            time.sleep(1.0 + i)
    return False


def _cleanup(store: Store, job_id: int, work: Path | None) -> None:
    if work and work.exists() and not _remove_tree(work):
        return  # on garde staging_dir en base : balayé au prochain démarrage du worker
    store.update(job_id, staging_dir="")


def _sweep_done_staging(store: Store) -> None:
    """Au démarrage : supprime les dossiers de staging des enregistrements terminés
    dont le nettoyage a échoué la fois précédente (verrou transitoire)."""
    for rec in store.all(limit=500):
        if rec.status == Status.DONE and rec.staging_dir:
            d = Path(rec.staging_dir)
            if not d.exists() or _remove_tree(d):
                store.update(rec.id, staging_dir="")


def _process(job: Recording, cfg: Config, store: Store, engine: WhisperEngine) -> None:
    work = Path(job.staging_dir) if job.staging_dir else None
    if not job.target_dir:
        raise RuntimeError(f"job {job.id} sans dossier cible")
    target = Path(job.target_dir)

    if work and not (work / "transcript.md").exists():
        _transcribe(job, cfg, store, engine, work)

    if not (target / "transcript.md").exists():
        if not work:
            raise FileNotFoundError("staging nettoyé mais transcript absent de la cible")
        _finalize(job, cfg, store, work, target)

    _maybe_summarize(job, cfg, store, target)

    fresh = store.get(job.id)
    if fresh and fresh.status in (Status.DONE, Status.SUMMARY_FAILED):
        _cleanup(store, job.id, work)


def _fail(store: Store, job: Recording, exc: Exception) -> None:
    current = store.get(job.id)
    stage = current.status if current else job.status
    failed_status = (
        Status.SUMMARY_FAILED if stage == Status.SUMMARIZING else Status.TRANSCRIBE_FAILED
    )
    store.set_status(job.id, failed_status, error=f"{type(exc).__name__}: {exc}"[:500])


# --------------------------------------------------------------------------- #
#  Boucle principale                                                          #
# --------------------------------------------------------------------------- #
def run(idle_exit: float = IDLE_EXIT_S, once: bool = False) -> int:
    setup("worker", console=True)
    if not _acquire_lock():
        log.info("Un worker est déjà actif — sortie.")
        return 0

    _lower_priority()
    log.info("Worker démarré (pid %d).", os.getpid())
    store = Store()
    _sweep_done_staging(store)
    engine: WhisperEngine | None = None
    idle_since = time.monotonic()

    try:
        while True:
            if WORKER_STOP_FLAG.exists():
                log.info("Arrêt demandé — sortie.")
                break
            _touch_lock()

            cfg = Config.load()
            job = store.next_job()

            if job is None:
                if once or time.monotonic() - idle_since > idle_exit:
                    log.info("File vide — sortie.")
                    FORCE_BATTERY_FLAG.unlink(missing_ok=True)  # dérogation batterie consommée
                    break
                time.sleep(4.0)
                continue
            idle_since = time.monotonic()

            if _recording_blocks(cfg):
                if job.status == Status.TRANSCRIBING:
                    store.set_status(job.id, Status.PAUSED_NO_AC)
                log.info("Enregistrement en cours — worker en retrait")
                if once:
                    break
                time.sleep(5.0)
                continue

            if _on_battery_blocked(cfg):
                if job.status not in (Status.PAUSED_NO_AC,) and job.status in Status.ACTIONABLE:
                    store.set_status(job.id, Status.PAUSED_NO_AC)
                    log.info("Sur batterie — job %d en pause", job.id)
                if once:
                    break
                time.sleep(BATTERY_POLL_S)
                continue

            try:
                if engine is None:
                    engine = _make_engine(cfg)
                # _process règle lui-même le statut de chaque étape (TRANSCRIBING /
                # SUMMARIZING / DONE) — inutile de sortir de PAUSED_NO_AC ici.
                _process(job, cfg, store, engine)
            except TranscriptionInterrupted:
                if WORKER_STOP_FLAG.exists():
                    log.info("Interrompu par l'arrêt — checkpoint conservé.")
                    break
                store.set_status(job.id, Status.PAUSED_NO_AC)
                log.info("Job %d interrompu (batterie/enregistrement) — reprise plus tard", job.id)
                time.sleep(BATTERY_POLL_S)
            except Exception as exc:  # noqa: BLE001
                log.exception("Échec du job %d", job.id)
                _fail(store, job, exc)

            if once:
                break
    finally:
        store.close()
        _release_lock()
        log.info("Worker terminé.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Worker de transcription Amphi")
    ap.add_argument("--idle-exit", type=float, default=IDLE_EXIT_S,
                    help="secondes d'inactivité avant sortie (0 = jamais)")
    ap.add_argument("--once", action="store_true", help="traiter un job puis sortir")
    args = ap.parse_args()

    WORKER_STOP_FLAG.unlink(missing_ok=True)
    idle = args.idle_exit if args.idle_exit > 0 else 10**9
    sys.exit(run(idle_exit=idle, once=args.once))


if __name__ == "__main__":
    main()
