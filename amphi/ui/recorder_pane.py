"""Colonne gauche : chrono, VU-mètre, boutons Enregistrer / Pause / Marqueur."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..audio import winmic
from ..audio.devices import pick_microphone
from ..audio.recorder import Recorder, RecorderState, RecordingResult
from ..config import Config
from ..models import RecordingMeta
from ..paths import STAGING_DIR
from .theme import COLORS
from .widgets import VUMeter


class RecorderPane(QFrame):
    recording_started = Signal()
    recording_finished = Signal(object)   # RecordingResult
    state_changed = Signal(str)
    silence_alert = Signal(bool)          # relayé à la fenêtre (notification tray)
    glitch_alert = Signal(int)            # coupures audio (buffer overflow)

    def __init__(self, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.cfg = cfg
        self.recorder = Recorder(self)

        self._build()
        self._wire()
        self._refresh_mic_label()
        self._apply_state(RecorderState.IDLE)

    # -- construction ----------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QLabel("ENREGISTREMENT")
        header.setObjectName("PaneHeader")
        root.addWidget(header)

        self._chrono = QLabel("00:00:00")
        self._chrono.setObjectName("Chrono")
        self._chrono.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._chrono)

        self._sub = QLabel("Prêt")
        self._sub.setObjectName("ChronoSub")
        self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._sub)

        self._vu = VUMeter()
        root.addWidget(self._vu)

        self._alert = QLabel("")
        self._alert.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._alert.setWordWrap(True)
        self._alert.setStyleSheet(
            f"color:white;background:{COLORS['rec']};border-radius:8px;"
            "padding:6px 8px;font-weight:700;font-size:12px;"
        )
        self._alert.setVisible(False)
        root.addWidget(self._alert)

        self._mic = QLabel("Micro : —")
        self._mic.setObjectName("MicName")
        self._mic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._mic)

        root.addStretch(1)

        self._record_btn = QPushButton("●  Enregistrer")
        self._record_btn.setObjectName("Record")
        self._record_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        root.addWidget(self._record_btn)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._pause_btn = QPushButton("⏸  Pause")
        self._pause_btn.setObjectName("Pause")
        self._pause_btn.setProperty("paused", False)
        self._marker_btn = QPushButton("＋  Marqueur")
        for b in (self._pause_btn, self._marker_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
        root.addLayout(row)

        self._hint = QLabel("Ctrl+M : marqueur · Ctrl+P : pause")
        self._hint.setObjectName("Hint")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._hint)

    def _wire(self) -> None:
        self.recorder.level.connect(self._vu.set_level)
        self.recorder.tick.connect(self._on_tick)
        self.recorder.state_changed.connect(self._apply_state)
        self.recorder.silence.connect(self._on_silence)
        self.recorder.glitch.connect(self._on_glitch)
        self.recorder.error.connect(self._on_error)

        self._record_btn.clicked.connect(self._on_record_clicked)
        self._pause_btn.clicked.connect(self.recorder.toggle_pause)
        self._marker_btn.clicked.connect(self._on_marker)

        self._sc_marker = QShortcut(QKeySequence("Ctrl+M"), self)
        self._sc_marker.activated.connect(self._on_marker)
        self._sc_pause = QShortcut(QKeySequence("Ctrl+P"), self)
        self._sc_pause.activated.connect(self._toggle_pause_if_recording)

    # -- actions -------------------------------------------------------
    def is_recording(self) -> bool:
        return self.recorder.state in (RecorderState.RECORDING, RecorderState.PAUSED)

    def _on_record_clicked(self) -> None:
        if self.is_recording():
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        try:
            device = pick_microphone(self.cfg.mic_name_hint, self.cfg.mic_device_id)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Micro", str(exc))
            return

        if winmic.is_muted() is True:
            resp = QMessageBox.question(
                self,
                "Micro coupé",
                "Ton micro est coupé dans Windows — l'enregistrement serait silencieux.\n\n"
                "Le réactiver et enregistrer ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
            if not winmic.set_muted(False):
                QMessageBox.warning(
                    self, "Micro",
                    "Impossible de réactiver le micro automatiquement — fais-le dans "
                    "les paramètres son de Windows, ou avec la touche micro du clavier.",
                )

        self.recorder.set_gain_db(self.cfg.mic_gain_db)
        staging = STAGING_DIR / f"rec_{datetime.now():%Y%m%d_%H%M%S}"
        try:
            self.recorder.start(device.index, staging)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Enregistrement", f"Démarrage impossible :\n{exc}")
            return
        self._mic.setText(f"Micro : {device.name}")
        self.recording_started.emit()

    def _stop(self) -> None:
        try:
            result: RecordingResult = self.recorder.stop()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Enregistrement", f"Arrêt impossible :\n{exc}")
            return
        self._vu.reset()
        self.recording_finished.emit(result)

    def resume_recording(self, staging_dir) -> bool:
        """Reprend un enregistrement suspendu (session précédente). Renvoie True si OK."""
        try:
            device = pick_microphone(self.cfg.mic_name_hint, self.cfg.mic_device_id)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Micro", str(exc))
            return False
        if winmic.is_muted() is True:
            winmic.set_muted(False)
        self.recorder.set_gain_db(self.cfg.mic_gain_db)
        try:
            self.recorder.resume_recording(device.index, staging_dir)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Reprendre", f"Reprise impossible :\n{exc}")
            return False
        self._mic.setText(f"Micro : {device.name}")
        self.recording_started.emit()
        return True

    def suspend(self):
        """Ferme le fichier mais garde l'enregistrement reprenable. Renvoie le staging."""
        try:
            path = self.recorder.suspend()
        except Exception:  # noqa: BLE001
            return None
        self._vu.reset()
        return path

    def _on_marker(self) -> None:
        offset = self.recorder.add_marker()
        if offset is None:
            return
        self._marker_btn.setText(f"＋  Marqueur  ({self.recorder.marker_count})")
        self._flash(self._marker_btn)

    def _toggle_pause_if_recording(self) -> None:
        if self.is_recording():
            self.recorder.toggle_pause()

    # -- vue ---------------------------------------------------------
    def _on_tick(self, seconds: float) -> None:
        self._chrono.setText(RecordingMeta.hhmmss(seconds))

    def _apply_state(self, state: str) -> None:
        recording = state in (RecorderState.RECORDING, RecorderState.PAUSED)
        paused = state == RecorderState.PAUSED

        self._record_btn.setText("■  Arrêter" if recording else "●  Enregistrer")
        self._record_btn.setProperty("recording", recording)
        self._restyle(self._record_btn)

        self._pause_btn.setEnabled(recording)
        self._marker_btn.setEnabled(recording)
        self._pause_btn.setText("▶  Reprendre" if paused else "⏸  Pause")
        self._pause_btn.setProperty("paused", paused)
        self._restyle(self._pause_btn)

        if state == RecorderState.RECORDING:
            self._sub.setText("●  Enregistrement en cours")
        elif state == RecorderState.PAUSED:
            self._sub.setText("⏸  En pause")
        else:
            self._chrono.setText("00:00:00")
            self._sub.setText("Prêt")
            self._marker_btn.setText("＋  Marqueur")
            self._vu.reset()
            self._alert.setVisible(False)

        self.state_changed.emit(state)

    def _on_silence(self, silent: bool) -> None:
        self._alert.setVisible(silent)
        if silent:
            self._alert.setText("⚠  AUCUN SON DÉTECTÉ\nmicro coupé ou mauvais périphérique ?")
        if self.recorder.state == RecorderState.RECORDING:
            self._sub.setStyleSheet(
                f"color:{COLORS['rec']};font-weight:700;" if silent else ""
            )
            self._sub.setText(
                "⚠  micro muet — vérifie !" if silent else "●  Enregistrement en cours"
            )
        self.silence_alert.emit(silent)

    def _on_glitch(self, total: int) -> None:
        self._alert.setVisible(True)
        self._alert.setText(
            f"⚠  {total} coupure(s) audio — CPU saturé.\n"
            "Décoche « transcription pendant l'enregistrement » (Réglages)."
        )
        self.glitch_alert.emit(total)

    def _refresh_mic_label(self) -> None:
        try:
            device = pick_microphone(self.cfg.mic_name_hint, self.cfg.mic_device_id)
            self._mic.setText(f"Micro : {device.name}")
        except RuntimeError:
            self._mic.setText("Micro : aucun détecté")

    def _on_error(self, message: str) -> None:
        QMessageBox.warning(self, "Audio", message)

    @staticmethod
    def _restyle(w) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    def _flash(self, widget) -> None:
        widget.setStyleSheet("background:#2f6b3a;border:1px solid #22c55e;")
        from PySide6.QtCore import QTimer

        QTimer.singleShot(220, lambda: widget.setStyleSheet(""))
