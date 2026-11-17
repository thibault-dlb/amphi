"""Backend de transcription faster-whisper (CTranslate2) : GPU NVIDIA (CUDA) si disponible,
sinon CPU. Interface volontairement fine pour rester interchangeable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from . import cuda

log = logging.getLogger(__name__)

DEVICES = ("auto", "cuda", "cpu")  # auto = GPU si utilisable, sinon CPU


class TranscriptionInterrupted(RuntimeError):
    """Levée quand should_cancel() passe à True (ex. passage sur batterie)."""


class GpuError(RuntimeError):
    """Le GPU a échoué en cours de transcription (mémoire saturée, pilote…).

    Le moteur est déjà repassé sur CPU : l'appelant peut relancer, la reprise se fait
    depuis le dernier checkpoint."""


@dataclass
class Segment:
    start: float
    end: float
    text: str

    def as_dict(self) -> dict:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


@dataclass
class TranscribeInfo:
    language: str
    language_probability: float
    duration: float


# Réglages fixes calibrés pour la précision sur du cours technique (cf. plan §5).
_DECODE = dict(
    best_of=5,
    patience=1.0,
    temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    condition_on_previous_text=True,
    compression_ratio_threshold=2.4,
    log_prob_threshold=-1.0,
    no_speech_threshold=0.6,
    word_timestamps=True,
    hallucination_silence_threshold=2.0,  # coupe le texte halluciné sur les silences d'amphi
)


class WhisperEngine:
    def __init__(
        self,
        model_name: str = "large-v3",
        compute_type: str = "int8",
        cpu_threads: int = 4,
        beam_size: int = 5,
        vad_filter: bool = True,
        device: str = "auto",
        gpu_compute_type: str = "float16",
        gpu_index: int = 0,
    ) -> None:
        self.model_name = model_name
        self.compute_type = compute_type          # quantification utilisée sur CPU
        self.cpu_threads = cpu_threads
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.device = device if device in DEVICES else "auto"
        self.gpu_compute_type = gpu_compute_type  # quantification utilisée sur GPU
        self.gpu_index = gpu_index
        self.active_device = ""                   # "cuda" | "cpu" une fois le modèle chargé
        self.active_compute_type = ""
        self._model = None
        self._gpu_failed = False                  # GPU abandonné pour la vie de ce moteur

    # -- chargement paresseux (télécharge le modèle au 1er appel) ----------
    def ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if self.device != "cpu" and not self._gpu_failed:
            ok, why = cuda.usable()
            if ok:
                try:
                    self._load("cuda", self.gpu_compute_type)
                    return
                except Exception as exc:  # noqa: BLE001
                    if self.device == "cuda":
                        raise
                    self._gpu_failed = True
                    log.warning("GPU inutilisable (%s) — repli sur le CPU.", exc)
            elif self.device == "cuda":
                raise RuntimeError(f"GPU demandé mais indisponible : {why}")
            else:
                log.info("Pas de GPU exploitable (%s) — CPU.", why)
        self._load("cpu", self.compute_type)

    def _load(self, device: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel

        if device == "cuda":
            log.info("Chargement du modèle %s sur GPU (%s)…", self.model_name, compute_type)
            model = WhisperModel(
                self.model_name,
                device="cuda",
                device_index=self.gpu_index,
                compute_type=compute_type,
                num_workers=1,
            )
        else:
            log.info(
                "Chargement du modèle %s sur CPU (%s, %d threads)…",
                self.model_name, compute_type, self.cpu_threads,
            )
            model = WhisperModel(
                self.model_name,
                device="cpu",
                compute_type=compute_type,
                cpu_threads=self.cpu_threads,
                num_workers=1,
            )
        self._model = model
        self.active_device = device
        self.active_compute_type = compute_type
        log.info("Modèle prêt (%s, %s).", device, compute_type)

    def unload(self) -> None:
        self._model = None
        self.active_device = ""
        self.active_compute_type = ""

    def describe(self) -> str:
        """Ex. « faster-whisper large-v3-turbo (GPU, float16) » — pour transcript.md."""
        where = "GPU" if self.active_device == "cuda" else "CPU"
        return f"faster-whisper {self.model_name} ({where}, {self.active_compute_type})"

    # -- transcription -------------------------------------------------------
    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str,
        initial_prompt: str = "",
        hotwords: str = "",
        on_segment: Callable[[Segment], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[Iterator[Segment], TranscribeInfo]:
        """Transcrit un fichier en une passe.

        Les timestamps renvoyés sont relatifs à `audio_path` (à décaler par
        l'appelant en cas de reprise sur un extrait tronqué).
        """
        self.ensure_loaded()
        assert self._model is not None

        opts = dict(_DECODE)
        opts["beam_size"] = self.beam_size
        opts["best_of"] = self.beam_size
        if hotwords:
            # hotwords (jusqu'à 223 tokens) + texte précédent (jusqu'à 223) + sot_sequence
            # approchent le max_length de 448 du modèle — le vocabulaire remplace alors
            # le contexte du texte précédent plutôt que de s'y ajouter.
            opts["condition_on_previous_text"] = False

        raw_segments, info = self._model.transcribe(
            str(audio_path),
            language=language,
            initial_prompt=initial_prompt or None,
            hotwords=hotwords or None,
            vad_filter=self.vad_filter,
            vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=200),
            **opts,
        )

        tinfo = TranscribeInfo(
            language=getattr(info, "language", language) or language,
            language_probability=float(getattr(info, "language_probability", 1.0) or 1.0),
            duration=float(getattr(info, "duration", 0.0) or 0.0),
        )

        def _iter() -> Iterator[Segment]:
            try:
                for s in raw_segments:
                    if should_cancel is not None and should_cancel():
                        raise TranscriptionInterrupted
                    text = (s.text or "").strip()
                    if not text:
                        continue
                    seg = Segment(start=float(s.start), end=float(s.end), text=text)
                    if on_segment is not None:
                        on_segment(seg)
                    yield seg
            except TranscriptionInterrupted:
                raise
            except RuntimeError as exc:
                # CTranslate2 signale les erreurs CUDA (mémoire, pilote, DLL) en RuntimeError.
                if self.active_device != "cuda" or self.device == "cuda":
                    raise
                log.warning("Erreur GPU en cours de transcription (%s) — bascule sur CPU.", exc)
                self._gpu_failed = True
                self.unload()
                raise GpuError(str(exc)) from exc

        return _iter(), tinfo
