"""Backend de transcription. Interface volontairement fine pour rester interchangeable
(un backend OpenVINO/iGPU pourrait s'y brancher si le CPU décevait au benchmark).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger(__name__)


class TranscriptionInterrupted(RuntimeError):
    """Levée quand should_cancel() passe à True (ex. passage sur batterie)."""


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
    ) -> None:
        self.model_name = model_name
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self._model = None

    # -- chargement paresseux (télécharge le modèle au 1er appel) ----------
    def ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        log.info(
            "Chargement du modèle %s (%s, %d threads)…",
            self.model_name, self.compute_type, self.cpu_threads,
        )
        self._model = WhisperModel(
            self.model_name,
            device="cpu",
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
            num_workers=1,
        )
        log.info("Modèle prêt.")

    def unload(self) -> None:
        self._model = None

    # -- transcription -------------------------------------------------------
    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str,
        initial_prompt: str = "",
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

        raw_segments, info = self._model.transcribe(
            str(audio_path),
            language=language,
            initial_prompt=initial_prompt or None,
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

        return _iter(), tinfo
