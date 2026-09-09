"""Widgets sur mesure : VU-mètre."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

from .theme import COLORS


class VUMeter(QWidget):
    """Barre de niveau horizontale, attaque rapide / retombée lente, tenue de crête."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(14)
        self.setMaximumHeight(18)
        self._level = 0.0
        self._target = 0.0
        self._peak = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(33)
        self._anim.timeout.connect(self._tick)
        self._anim.start()

    def set_level(self, rms: float, peak: float) -> None:
        self._target = min(1.0, max(0.0, rms * 1.7))
        self._peak = max(self._peak, min(1.0, peak * 1.7))

    def reset(self) -> None:
        self._level = self._target = self._peak = 0.0
        self.update()

    def _tick(self) -> None:
        k = 0.55 if self._target > self._level else 0.18
        self._level += (self._target - self._level) * k
        self._peak = max(0.0, self._peak - 0.012)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        radius = h / 2

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(COLORS["input"]))
        p.drawRoundedRect(0, 0, w, h, radius, radius)

        if self._level > 0.001:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0.0, QColor(COLORS["green"]))
            grad.setColorAt(0.55, QColor(COLORS["green"]))
            grad.setColorAt(0.78, QColor(COLORS["amber"]))
            grad.setColorAt(1.0, QColor(COLORS["rec"]))
            p.setBrush(grad)
            fill_w = max(h, int(w * self._level))
            p.drawRoundedRect(0, 0, fill_w, h, radius, radius)

        if self._peak > 0.02:
            x = min(w - 2, int(w * self._peak))
            p.setPen(QColor(COLORS["text"]))
            p.drawLine(x, 2, x, h - 2)
        p.end()
