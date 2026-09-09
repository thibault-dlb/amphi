"""Capture micro robuste : pause/reprise sans artefact, marqueurs, résistance au crash.

- Le flux PortAudio reste ouvert pendant les pauses (on jette les frames) : aucun
  clic ni perte au redémarrage.
- L'écriture disque se fait dans un thread dédié, jamais dans le callback audio.
- Un sidecar `recording.json` est réécrit toutes les 2 s : si l'app est tuée, on
  garde durée + marqueurs, et le WAV est réparable par ffmpeg.
- On enregistre à la fréquence native du micro ; le rééchantillonnage vers 16 kHz
  est fait par faster-whisper au moment de la transcription.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, QTimer, Signal

from .. import __version__
from ..models import Marker

log = logging.getLogger(__name__)

CAPTURE_WAV = "capture.wav"
SIDECAR = "recording.json"
_CANDIDATE_RATES = (48000, 44100, 32000, 16000)

# En dessous de ce niveau crête (~ -56 dBFS) pendant _SILENCE_SECONDS d'affilée,
# on considère que le micro ne délivre plus rien (coupé, mauvais périphérique…).
_SILENCE_PEAK = 0.0015
_SILENCE_SECONDS = 12.0


@dataclass
class RecordingResult:
    staging_dir: Path
    wav_path: Path
    duration_s: float
    sample_rate: int
    markers: list[Marker] = field(default_factory=list)


class RecorderState:
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"


class Recorder(QObject):
    """Un enregistrement à la fois. Vit sur le thread principal Qt."""

    level = Signal(float, float)      # rms, peak  (0..1)
    tick = Signal(float)             # secondes capturées (hors pauses)
    state_changed = Signal(str)
    silence = Signal(bool)           # True = plus aucun signal micro depuis un moment
    glitch = Signal(int)             # cumul de dépassements de buffer (audio perdu)
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = RecorderState.IDLE
        self._lock = threading.Lock()
        self._q: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._wav = None  # soundfile.SoundFile
        self._writer: threading.Thread | None = None

        self._paused = False
        self._captured_frames = 0
        self._written_frames = 0
        self._sr = 48000
        self._last_rms = 0.0
        self._last_peak = 0.0
        self._status_flags = ""
        self._markers: list[Marker] = []
        self._staging_dir: Path | None = None
        self._started_at = 0.0
        self._last_sidecar = 0.0
        self._audible_at = 0.0
        self._silence_flagged = False
        self._gain = 1.0
        self._glitches = 0
        self._glitches_reported = 0

        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._on_poll)

    # -- état -----------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def markers(self) -> list[Marker]:
        return list(self._markers)

    @property
    def marker_count(self) -> int:
        return len(self._markers)

    def set_gain_db(self, db: float) -> None:
        """Amplification numérique appliquée à la capture (0 = neutre). Prend effet
        même en cours d'enregistrement."""
        self._gain = 10.0 ** (max(0.0, min(30.0, db)) / 20.0)

    def _set_state(self, s: str) -> None:
        self._state = s
        self.state_changed.emit(s)

    # -- cycle de vie -------------------------------------------------------
    def _reset_counters(self) -> None:
        self._paused = False
        self._last_rms = self._last_peak = 0.0
        self._status_flags = ""
        self._started_at = time.time()
        self._last_sidecar = 0.0
        self._audible_at = time.monotonic()
        self._silence_flagged = False
        self._glitches = 0
        self._glitches_reported = 0

    def _begin_capture(self, device_index: int) -> None:
        self._writer = threading.Thread(
            target=self._writer_loop, name="amphi-wav-writer", daemon=True
        )
        self._writer.start()
        try:
            self._stream = sd.InputStream(
                samplerate=self._sr,
                device=device_index,
                channels=1,
                dtype="int16",
                blocksize=0,
                callback=self._callback,
            )
            self._stream.start()
        except Exception:  # noqa: BLE001
            self._teardown_partial()
            raise  # le pane affiche l'erreur ; le signal error reste pour l'asynchrone

    def start(self, device_index: int, staging_dir: Path) -> None:
        if self._state in (RecorderState.RECORDING, RecorderState.PAUSED):
            raise RuntimeError("Un enregistrement est déjà en cours.")
        staging_dir.mkdir(parents=True, exist_ok=True)
        self._staging_dir = staging_dir
        self._sr = self._negotiate_samplerate(device_index)

        import soundfile as sf

        self._wav = sf.SoundFile(
            str(staging_dir / CAPTURE_WAV),
            mode="w",
            samplerate=self._sr,
            channels=1,
            subtype="PCM_16",
        )
        self._reset_counters()
        self._captured_frames = 0
        self._written_frames = 0
        self._markers = []
        self._begin_capture(device_index)

        self._write_sidecar()
        self._poll.start()
        self._set_state(RecorderState.RECORDING)
        log.info("Enregistrement démarré : %s @ %d Hz", staging_dir.name, self._sr)

    def resume_recording(self, device_index: int, staging_dir: Path) -> None:
        """Reprend un enregistrement mis en pause lors d'une session précédente :
        rouvre `capture.wav` en ajout et restaure durée + marqueurs. État = PAUSED."""
        if self._state in (RecorderState.RECORDING, RecorderState.PAUSED):
            raise RuntimeError("Un enregistrement est déjà en cours.")

        side = json.loads((staging_dir / SIDECAR).read_text(encoding="utf-8"))
        self._sr = int(side.get("sample_rate", 48000))
        try:
            sd.check_input_settings(
                device=device_index, samplerate=self._sr, channels=1, dtype="int16"
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Le micro ne peut pas reprendre à {self._sr} Hz — "
                "termine cet enregistrement tel quel."
            ) from exc

        import soundfile as sf

        self._staging_dir = staging_dir
        self._wav = sf.SoundFile(str(staging_dir / CAPTURE_WAV), mode="r+")
        self._wav.seek(0, sf.SEEK_END)

        self._reset_counters()
        self._captured_frames = int(round(float(side.get("captured_seconds", 0.0)) * self._sr))
        self._written_frames = self._captured_frames
        self._markers = [Marker(**m) for m in side.get("markers", [])]
        self._paused = True  # on reprend là où l'utilisateur avait mis en pause

        self._begin_capture(device_index)
        self._write_sidecar(force=True)
        self._poll.start()
        self._set_state(RecorderState.PAUSED)
        log.info(
            "Enregistrement repris : %s (à %.0f s, %d marqueur(s))",
            staging_dir.name, self._captured_frames / self._sr, len(self._markers),
        )

    def suspend(self) -> Path:
        """Ferme proprement le fichier mais garde l'enregistrement REPRENABLE
        (drapeau `suspended` dans le sidecar). Ne produit pas de RecordingResult."""
        if self._state not in (RecorderState.RECORDING, RecorderState.PAUSED):
            raise RuntimeError("Aucun enregistrement à suspendre.")
        assert self._staging_dir is not None
        staging = self._staging_dir
        self._teardown_stream_and_wav()
        duration = self._written_frames / self._sr
        self._write_sidecar(force=True, duration=duration, suspended=True)
        log.info("Enregistrement suspendu : %s (%.0f s)", staging.name, duration)
        self._set_state(RecorderState.IDLE)
        return staging

    def pause(self) -> None:
        if self._state != RecorderState.RECORDING:
            return
        with self._lock:
            self._paused = True
        self._write_sidecar(force=True)
        self._set_state(RecorderState.PAUSED)

    def resume(self) -> None:
        if self._state != RecorderState.PAUSED:
            return
        with self._lock:
            self._paused = False
        self._audible_at = time.monotonic()  # redonne 12 s avant de crier au silence
        self._write_sidecar(force=True)
        self._set_state(RecorderState.RECORDING)

    def toggle_pause(self) -> None:
        self.resume() if self._state == RecorderState.PAUSED else self.pause()

    def add_marker(self, label: str = "important", note: str = "") -> float | None:
        if self._state not in (RecorderState.RECORDING, RecorderState.PAUSED):
            return None
        with self._lock:
            offset = self._captured_frames / self._sr
        self._markers.append(Marker(offset_s=round(offset, 2), label=label, note=note))
        self._write_sidecar(force=True)
        log.info("Marqueur @ %.1fs (%s)", offset, label)
        return offset

    def _teardown_stream_and_wav(self) -> None:
        self._poll.stop()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                log.exception("Fermeture du flux")
            self._stream = None

        self._q.put(None)  # signal de fin au writer
        if self._writer is not None:
            self._writer.join(timeout=120)  # laisse le temps d'écrire un gros backlog
            if self._writer.is_alive():
                log.error(
                    "Writer WAV toujours actif — %d bloc(s) audio en attente", self._q.qsize()
                )
            self._writer = None

        if self._wav is not None:
            try:
                self._wav.flush()
                self._wav.close()
            except Exception:  # noqa: BLE001
                log.exception("Fermeture du WAV")
            self._wav = None

    def stop(self) -> RecordingResult:
        if self._state not in (RecorderState.RECORDING, RecorderState.PAUSED):
            raise RuntimeError("Aucun enregistrement à arrêter.")
        self._teardown_stream_and_wav()

        assert self._staging_dir is not None
        duration = self._written_frames / self._sr
        result = RecordingResult(
            staging_dir=self._staging_dir,
            wav_path=self._staging_dir / CAPTURE_WAV,
            duration_s=round(duration, 2),
            sample_rate=self._sr,
            markers=list(self._markers),
        )
        self._write_sidecar(force=True, stopped=True, duration=duration)
        if self._silence_flagged:
            self.silence.emit(False)
        self._set_state(RecorderState.STOPPED)
        log.info("Enregistrement arrêté : %.1fs, %d marqueur(s)", duration, len(self._markers))
        self._set_state(RecorderState.IDLE)
        return result

    # -- interne ----------------------------------------------------------
    def _negotiate_samplerate(self, device_index: int) -> int:
        for rate in _CANDIDATE_RATES:
            try:
                sd.check_input_settings(
                    device=device_index, samplerate=rate, channels=1, dtype="int16"
                )
                return rate
            except Exception:  # noqa: BLE001
                continue
        info = sd.query_devices(device_index)
        return int(info["default_samplerate"])

    def _callback(self, indata, frames, time_info, status) -> None:  # PortAudio thread
        if status:
            self._status_flags = str(status)
            if getattr(status, "input_overflow", False) and not self._paused:
                self._glitches += 1

        if self._gain != 1.0 and indata.size:
            data = np.clip(
                indata.astype(np.int32) * self._gain, -32768, 32767
            ).astype(np.int16)
        else:
            data = indata

        mono = data.reshape(-1)
        if mono.size:
            x = mono.astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(x * x)))
            peak = float(np.max(np.abs(x)))
        else:
            rms = peak = 0.0
        with self._lock:
            self._last_rms = rms
            self._last_peak = peak
            if not self._paused:
                self._captured_frames += frames
                paused = False
            else:
                paused = True
        if not paused:
            self._q.put(data.copy() if data is indata else data)

    def _writer_loop(self) -> None:
        while True:
            chunk = self._q.get()
            if chunk is None:
                break
            try:
                self._wav.write(chunk)
                with self._lock:
                    self._written_frames += len(chunk)
            except Exception:  # noqa: BLE001
                log.exception("Écriture WAV")

    def _on_poll(self) -> None:
        with self._lock:
            rms, peak = self._last_rms, self._last_peak
            secs = self._captured_frames / self._sr
            glitches = self._glitches
        if self._state == RecorderState.PAUSED:
            rms = peak = 0.0
        self.level.emit(rms, peak)
        self.tick.emit(secs)

        if glitches > self._glitches_reported:
            self._glitches_reported = glitches
            self.glitch.emit(glitches)
            log.warning("Dépassement de buffer micro (audio perdu) — total %d", glitches)

        now = time.monotonic()
        if self._state == RecorderState.RECORDING:
            if peak >= _SILENCE_PEAK:
                self._audible_at = now
                if self._silence_flagged:
                    self._silence_flagged = False
                    self.silence.emit(False)
            elif not self._silence_flagged and now - self._audible_at >= _SILENCE_SECONDS:
                self._silence_flagged = True
                self.silence.emit(True)
                log.warning(
                    "Aucun signal micro depuis %.0f s — micro coupé ou muet ?",
                    _SILENCE_SECONDS,
                )

        if now - self._last_sidecar >= 2.0:
            self._write_sidecar()
            self._last_sidecar = now
            if self._wav is not None:
                try:
                    self._wav.flush()
                except Exception:  # noqa: BLE001
                    pass

    def _write_sidecar(
        self,
        *,
        force: bool = False,
        stopped: bool = False,
        suspended: bool = False,
        duration: float | None = None,
    ) -> None:
        if self._staging_dir is None:
            return
        with self._lock:
            captured = self._captured_frames / self._sr
            paused = self._paused
        payload = {
            "schema": 1,
            "app_version": __version__,
            "started_at": self._started_at,
            "sample_rate": self._sr,
            "captured_seconds": round(duration if duration is not None else captured, 3),
            "paused": paused,
            "stopped": stopped,
            "suspended": suspended,
            "status_flags": self._status_flags,
            "wav": CAPTURE_WAV,
            "markers": [
                {"offset_s": m.offset_s, "label": m.label, "note": m.note} for m in self._markers
            ],
        }
        if stopped:
            payload["stopped_at"] = time.time()
        if suspended:
            payload["suspended_at"] = time.time()
        try:
            tmp = self._staging_dir / (SIDECAR + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._staging_dir / SIDECAR)
        except Exception:  # noqa: BLE001
            log.exception("Écriture du sidecar")

    def _teardown_partial(self) -> None:
        self._poll.stop()
        try:
            self._q.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass
        if self._writer is not None:
            self._writer.join(timeout=5)
            self._writer = None
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:  # noqa: BLE001
                pass
            self._wav = None
