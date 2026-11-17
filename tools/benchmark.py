"""Mesure le débit réel de faster-whisper sur CETTE machine (GPU CUDA et CPU) et fixe
les défauts du moteur dans config.json.

    python tools/benchmark.py                 # complet (GPU + CPU)
    python tools/benchmark.py --quick         # rapide, moins de configurations
    python tools/benchmark.py --gpu-only      # saute les mesures CPU (lentes)
    python tools/benchmark.py mon_extrait.wav # sur un vrai enregistrement

Aucun benchmark public ne couvre une machine précise : ce chiffre-là est le seul qui compte.
"""

from __future__ import annotations

import argparse
import gc
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from amphi.config import Config  # noqa: E402
from amphi.transcribe import cuda  # noqa: E402

_FR_TEXT = """
Considérons un système linéaire invariant dans le temps décrit par sa fonction de transfert.
La stabilité au sens entrée bornée sortie bornée impose que tous les pôles soient à partie
réelle strictement négative. Pour un régulateur proportionnel intégral, le terme intégral
annule l'erreur statique mais introduit un déphasage qui réduit la marge de phase.
En thermodynamique, le second principe stipule que l'entropie d'un système isolé ne peut
que croître ; le rendement d'un cycle de Carnot ne dépend que des températures des sources
chaude et froide. Passons maintenant à la mécanique des fluides. L'équation de Navier-Stokes
exprime la conservation de la quantité de mouvement pour un fluide newtonien incompressible.
Le nombre de Reynolds compare les forces d'inertie aux forces visqueuses et gouverne la
transition vers la turbulence. En analyse numérique, la méthode des éléments finis discrétise
le domaine en un maillage et cherche une solution approchée dans un espace de dimension finie.
La convergence dépend de la régularité de la solution exacte et de la taille caractéristique
des mailles. Pour l'algorithmique, la complexité amortie d'une séquence d'opérations se
calcule par la méthode du potentiel, en attribuant un crédit à chaque état de la structure.
"""


def _make_sample(seconds: int) -> Path:
    """Génère un WAV de parole française via SAPI (System.Speech).

    ~15 caractères de texte par seconde de parole (SAPI Rate=1).
    """
    tmp = Path(tempfile.gettempdir()) / "amphi_bench_sample.wav"
    txt = Path(tempfile.gettempdir()) / "amphi_bench_text.txt"
    base = " ".join(_FR_TEXT.split())
    target_chars = int(seconds * 20)  # ~20 car./s mesuré sur les voix FR de Windows
    if target_chars <= len(base):
        cut = base[:target_chars]
        content = cut.rsplit(".", 1)[0] + "." if "." in cut else cut
    else:
        content = (base + " ") * (target_chars // len(base) + 1)
        content = content[:target_chars]
    txt.write_text(content, encoding="utf-8")
    ps = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fr = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Culture.Name -like 'fr*' }} | Select-Object -First 1
if ($fr) {{ $s.SelectVoice($fr.VoiceInfo.Name) }} else {{ Write-Host 'AUCUNE VOIX FR - WER non representatif' }}
$s.Rate = 1
$s.SetOutputToWaveFile('{tmp.as_posix()}')
$s.Speak([System.IO.File]::ReadAllText('{txt.as_posix()}'))
$s.Dispose()
"""
    print("Génération d'un échantillon de parole…", flush=True)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        check=True, capture_output=True, text=True,
        creationflags=0x08000000,  # CREATE_NO_WINDOW
    )
    return tmp


class _RamSampler(threading.Thread):
    """Suit le pic de RSS pendant une transcription. (`_stop` est réservé par Thread.)"""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._halt = threading.Event()
        self.peak_mb = 0.0

    def run(self) -> None:
        import psutil

        p = psutil.Process()
        while not self._halt.is_set():
            self.peak_mb = max(self.peak_mb, p.memory_info().rss / 1e6)
            time.sleep(0.4)

    def finish(self) -> float:
        self._halt.set()
        self.join(timeout=2)
        return self.peak_mb


MODEL = "large-v3-turbo"  # surchargé par --model


def _vram_used_mb() -> int | None:
    """VRAM occupée sur le GPU 0 (nvidia-smi), None si indisponible."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--id=0", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, creationflags=0x08000000,
        )
        return int(float(out.stdout.strip())) if out.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _make_model(device: str, compute_type: str, threads: int):
    from faster_whisper import WhisperModel

    if device == "cuda":
        return WhisperModel(MODEL, device="cuda", compute_type=compute_type, num_workers=1)
    return WhisperModel(MODEL, device="cpu", compute_type=compute_type,
                        cpu_threads=threads, num_workers=1)


def _bench_one(sample: Path, device: str, compute_type: str, threads: int,
               *, warmup: bool = False) -> dict:
    model = _make_model(device, compute_type, threads)
    if warmup:  # construit le modèle (télécharge si besoin) — pas de mesure
        del model
        gc.collect()
        return {}
    if device == "cuda":  # passe à blanc : l'init des noyaux CUDA/cuDNN ne doit pas être mesurée
        list(model.transcribe(str(sample), language="fr", beam_size=5, vad_filter=True)[0])
    ram = _RamSampler()
    ram.start()
    t0 = time.perf_counter()
    segments, info = model.transcribe(str(sample), language="fr", beam_size=5,
                                      vad_filter=True)
    words = sum(len(s.text.split()) for s in segments)
    wall = time.perf_counter() - t0
    peak = ram.finish()
    vram = _vram_used_mb() if device == "cuda" else None  # modèle encore chargé
    audio = float(info.duration) or 1.0
    del model
    gc.collect()
    return {
        "device": device,
        "compute_type": compute_type,
        "threads": threads if device == "cpu" else 0,
        "vram_mb": vram,
        "wall_s": round(wall, 1),
        "audio_s": round(audio, 1),
        "rtf": round(wall / audio, 2),
        "x_realtime": round(audio / wall, 2),
        "peak_ram_mb": round(peak),
        "words": words,
    }


def main() -> int:
    global MODEL
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", nargs="?", help="WAV/FLAC à utiliser (sinon : échantillon SAPI)")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--gpu-only", action="store_true", help="saute les mesures CPU")
    ap.add_argument("--model", default=None, help="modèle à mesurer (défaut : config.json)")
    args = ap.parse_args()

    MODEL = args.model or Config.load().engine.model or MODEL

    if args.audio:
        sample = Path(args.audio)
        if not sample.exists():
            print(f"Fichier introuvable : {sample}")
            return 2
    else:
        sample = _make_sample(30 if args.quick else 55)

    # (device, compute_type, threads CPU) — les threads ne comptent pas sur GPU
    matrix: list[tuple[str, str, int]] = []
    gpu_ok, gpu_why = cuda.usable()
    if gpu_ok:
        info = cuda.gpu_info()
        print(f"GPU : {info.name} · {info.vram_mb} Mo" if info else f"GPU : {gpu_why}")
        matrix += [("cuda", "float16", 0)]
        if not args.quick:
            matrix += [("cuda", "int8_float16", 0)]
    else:
        print(f"GPU inutilisable — mesures CPU seulement ({gpu_why})")
    if not args.gpu_only or not gpu_ok:
        if args.quick:
            matrix += [("cpu", "int8", 8)]
        else:
            matrix += [("cpu", "int8", 4), ("cpu", "int8", 8), ("cpu", "int8", 12)]

    print(f"\nModèle : {MODEL}  ·  échantillon : {sample}  ·  {len(matrix)} configurations")
    print("(beam_size=5, VAD — les réglages réels de l'app)\n")
    print("Préchauffage (télécharge le modèle au 1er passage)…", flush=True)
    try:
        _bench_one(sample, matrix[0][0], matrix[0][1], matrix[0][2], warmup=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Échec du préchauffage : {exc}")
        return 1

    results = []
    for dev, ct, th in matrix:
        label = "GPU" if dev == "cuda" else f"CPU {th} threads"
        print(f"  → {label:14s} {ct:14s} … ", end="", flush=True)
        try:
            r = _bench_one(sample, dev, ct, th)
            results.append(r)
            mem = f"{r['vram_mb']} Mo VRAM" if r["vram_mb"] else f"{r['peak_ram_mb']} Mo RAM"
            print(f"{r['x_realtime']}× temps réel  (RTF {r['rtf']}, {mem})")
        except Exception as exc:  # noqa: BLE001
            print(f"échec ({exc})")

    if not results:
        print("Aucun résultat.")
        return 1

    def _name(r: dict) -> str:
        return f"{'GPU' if r['device'] == 'cuda' else 'CPU'} {r['compute_type']}" + (
            f" /{r['threads']}t" if r["device"] == "cpu" else ""
        )

    best = min(results, key=lambda r: r["wall_s"])
    best_gpu = min((r for r in results if r["device"] == "cuda"), key=lambda r: r["wall_s"],
                   default=None)
    best_cpu = min((r for r in results if r["device"] == "cpu"), key=lambda r: r["wall_s"],
                   default=None)

    print("\n" + "=" * 64)
    print(f"{'config':26s}{'× temps réel':>14s}{'RTF':>8s}{'mémoire':>14s}")
    for r in sorted(results, key=lambda r: r["wall_s"]):
        star = "  ←" if r is best else ""
        mem = f"{r['vram_mb']} Mo VRAM" if r["vram_mb"] else f"{r['peak_ram_mb']} Mo RAM"
        print(f"{_name(r):26s}{r['x_realtime']:>13}×{r['rtf']:>8}{mem:>16}{star}")
    print("=" * 64)
    print(f"\nMeilleur : {_name(best)}")
    print(f"≈ {best['rtf'] * 60:.0f} min de calcul pour 1 h de cours.\n")

    cfg = Config.load()
    parts = []
    if best_gpu:
        cfg.engine.device = "auto"          # GPU d'abord, CPU en secours
        cfg.engine.gpu_compute_type = best_gpu["compute_type"]
        parts.append(f"GPU {best_gpu['compute_type']} : {best_gpu['x_realtime']}× temps réel "
                     f"(~{best_gpu['rtf'] * 60:.0f} min/h)")
    if best_cpu:
        cfg.engine.compute_type = best_cpu["compute_type"]
        cfg.engine.cpu_threads = best_cpu["threads"]
        parts.append(f"CPU {best_cpu['compute_type']}/{best_cpu['threads']}t : "
                     f"{best_cpu['x_realtime']}× (~{best_cpu['rtf'] * 60:.0f} min/h)")
    cfg.engine.benchmarked = True
    cfg.engine.benchmark_notes = (
        f"{MODEL} · " + " · ".join(parts) + f". Mesuré le {time.strftime('%Y-%m-%d')}."
    )
    cfg.save()
    print(f"config.json mis à jour → {cfg.engine.benchmark_notes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
