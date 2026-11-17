"""Détection et préparation du GPU NVIDIA pour CTranslate2 (faster-whisper).

CTranslate2 s'appuie sur cuBLAS et cuDNN. Plutôt que d'installer le toolkit CUDA, on
utilise les wheels pip `nvidia-cublas-cu12` et `nvidia-cudnn-cu12` (cf. requirements.txt) :
leurs DLL vivent dans `site-packages\\nvidia\\<lib>\\bin`, un dossier que Windows ne
parcourt pas tout seul. `prepare()` l'ajoute au chemin de recherche des DLL et précharge
les bibliothèques ; seul le pilote NVIDIA doit être présent sur la machine.

Module sans dépendance lourde : importé par le worker et par le benchmark.
"""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# paquets nvidia-*-cu12 dont on expose le dossier de DLL
_NVIDIA_PACKAGES = ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc")
# DLL préchargées (ordre : les dépendances d'abord). Les motifs couvrent cu12 / cu13.
_PRELOAD_PATTERNS = ("cudart64_*.dll", "cublasLt64_*.dll", "cublas64_*.dll", "cudnn64_*.dll")

_prepared: tuple[bool, str] | None = None


@dataclass
class GpuInfo:
    name: str
    vram_mb: int
    driver: str


def _nvidia_bin_dirs() -> list[Path]:
    """Dossiers de DLL des wheels nvidia-* installés dans l'environnement courant."""
    try:
        spec = importlib.util.find_spec("nvidia")
    except (ImportError, ValueError):
        return []
    if spec is None or not spec.submodule_search_locations:
        return []
    sub = "bin" if sys.platform == "win32" else "lib"
    dirs: list[Path] = []
    for base in spec.submodule_search_locations:
        for pkg in _NVIDIA_PACKAGES:
            d = Path(base) / pkg / sub
            if d.is_dir():
                dirs.append(d)
    return dirs


def prepare() -> tuple[bool, str]:
    """Rend les DLL CUDA visibles pour CTranslate2. Renvoie (ok, détail). Idempotent."""
    global _prepared
    if _prepared is not None:
        return _prepared
    _prepared = _prepare()
    return _prepared


def _prepare() -> tuple[bool, str]:
    dirs = _nvidia_bin_dirs()
    if not dirs:
        return False, "wheels nvidia-cublas-cu12 / nvidia-cudnn-cu12 absents (pip install -r requirements.txt)"
    if sys.platform != "win32":
        return True, "bibliothèques nvidia trouvées"

    for d in dirs:
        try:
            os.add_dll_directory(str(d))
        except OSError:
            log.debug("add_dll_directory(%s) refusé", d, exc_info=True)
    os.environ["PATH"] = os.pathsep.join([*(str(d) for d in dirs), os.environ.get("PATH", "")])

    loaded: list[str] = []
    for pattern in _PRELOAD_PATTERNS:
        for d in dirs:
            for dll in sorted(d.glob(pattern)):
                try:
                    ctypes.WinDLL(str(dll))
                    loaded.append(dll.name)
                except OSError as exc:
                    return False, f"{dll.name} non chargeable : {exc}"
    needed = ("cublas", "cudnn")
    missing = [n for n in needed if not any(n in name.lower() for name in loaded)]
    if missing:
        return False, "DLL manquantes : " + ", ".join(missing)
    return True, ", ".join(loaded)


def usable() -> tuple[bool, str]:
    """Le GPU est-il utilisable par faster-whisper ? Renvoie (ok, explication)."""
    try:
        import ctranslate2
    except ImportError as exc:
        return False, f"ctranslate2 indisponible : {exc}"
    ok, detail = prepare()
    if not ok:
        return False, detail
    try:
        count = ctranslate2.get_cuda_device_count()
    except Exception as exc:  # noqa: BLE001
        return False, f"détection CUDA impossible : {exc}"
    if count < 1:
        return False, "aucun GPU CUDA détecté (pilote NVIDIA absent ou trop ancien)"
    return True, f"{count} GPU CUDA"


def supported_compute_types(device: str = "cuda") -> list[str]:
    try:
        import ctranslate2

        return sorted(ctranslate2.get_supported_compute_types(device))
    except Exception:  # noqa: BLE001
        return []


def gpu_info(index: int = 0) -> GpuInfo | None:
    """Nom / VRAM / pilote via nvidia-smi (meilleur effort, None si absent)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--id={index}", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000 if sys.platform == "win32" else 0,  # CREATE_NO_WINDOW
        )
        if out.returncode != 0:
            return None
        name, mem, driver = (p.strip() for p in out.stdout.strip().split(",")[:3])
        return GpuInfo(name=name, vram_mb=int(float(mem)), driver=driver)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
