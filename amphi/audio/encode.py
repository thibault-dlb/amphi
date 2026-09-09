"""Encodage FLAC et réparation de WAV interrompus, via ffmpeg (déjà sur le PATH)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000  # évite un flash de console quand lancé depuis pythonw


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_duration(path: Path) -> float | None:
    if shutil.which("ffprobe") is None:
        return None
    cp = _run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ]
    )
    if cp.returncode != 0:
        return None
    try:
        return float(json.loads(cp.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def repair_wav(broken: Path, fixed: Path) -> bool:
    """Réécrit l'en-tête d'un WAV tronqué (app tuée en cours d'écriture)."""
    cp = _run(["ffmpeg", "-y", "-err_detect", "ignore_err", "-i", str(broken),
               "-c", "copy", str(fixed)])
    if cp.returncode == 0 and fixed.exists() and fixed.stat().st_size > 1024:
        return True
    # dernier recours : redécoder en PCM
    cp = _run(["ffmpeg", "-y", "-err_detect", "ignore_err", "-i", str(broken),
               "-c:a", "pcm_s16le", str(fixed)])
    return cp.returncode == 0 and fixed.exists() and fixed.stat().st_size > 1024


def to_flac(src: Path, dst: Path) -> bool:
    """WAV -> FLAC sans perte (compression_level 8). Renvoie True si OK."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cp = _run(["ffmpeg", "-y", "-i", str(src), "-c:a", "flac",
               "-compression_level", "8", str(dst)])
    ok = cp.returncode == 0 and dst.exists() and dst.stat().st_size > 1024
    if not ok:
        log.error("FLAC échoué (%s): %s", src.name, cp.stderr[-500:])
    return ok


def normalize_speech(src: Path, dst: Path) -> bool:
    """Remonte le niveau de parole (locuteur lointain, micro peu sensible) avant Whisper.

    highpass 80 Hz (coupe rumble/ventilation) + speechnorm (compresseur de parole avec
    limiteur). Ne modifie pas le timing → les timestamps restent valides. L'archive FLAC
    reste faite à partir de l'original, pas de ce fichier.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    cp = _run([
        "ffmpeg", "-y", "-i", str(src),
        "-af", "highpass=f=80,speechnorm=e=10:r=0.0004:l=1",
        "-c:a", "pcm_s16le", "-ac", "1", str(dst),
    ])
    return cp.returncode == 0 and dst.exists() and dst.stat().st_size > 1024


def trim_from(src: Path, dst: Path, start_s: float) -> bool:
    """Extrait src à partir de start_s vers un WAV PCM (pour reprendre une transcription).

    Ré-encodage en PCM : seek sample-accurate, indispensable pour l'alignement des
    timestamps au recollage.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    cp = _run(["ffmpeg", "-y", "-ss", f"{max(0.0, start_s):.3f}", "-i", str(src),
               "-c:a", "pcm_s16le", "-ac", "1", str(dst)])
    return cp.returncode == 0 and dst.exists() and dst.stat().st_size > 1024


def to_opus(src: Path, dst: Path, bitrate: str = "24k") -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cp = _run(["ffmpeg", "-y", "-i", str(src), "-c:a", "libopus", "-b:a", bitrate,
               "-application", "voip", str(dst)])
    return cp.returncode == 0 and dst.exists() and dst.stat().st_size > 512


def archive_audio(src_wav: Path, dst_dir: Path, fmt: str) -> Path | None:
    """Produit le fichier audio final dans dst_dir selon le format demandé.

    fmt : "flac" | "wav" | "opus" | "none". Renvoie le chemin créé (ou None si "none").
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    if fmt == "none":
        return None
    if fmt == "wav":
        target = dst_dir / "audio.wav"
        shutil.copy2(src_wav, target)
        return target
    if fmt == "opus":
        target = dst_dir / "audio.opus"
        return target if to_opus(src_wav, target) else _fallback_wav(src_wav, dst_dir)
    target = dst_dir / "audio.flac"
    return target if to_flac(src_wav, target) else _fallback_wav(src_wav, dst_dir)


def _fallback_wav(src_wav: Path, dst_dir: Path) -> Path:
    log.warning("Repli sur WAV brut pour %s", dst_dir)
    target = dst_dir / "audio.wav"
    shutil.copy2(src_wav, target)
    return target
