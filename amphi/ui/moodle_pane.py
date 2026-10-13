"""Tuile « MOODLE » (colonne de gauche) : synchro devoirs → agenda Apple, état et tableau."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout

from .moodle_table import MoodleTable
from .theme import COLORS


class MoodlePane(QFrame):
    sync_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        header = QLabel("MOODLE")
        header.setObjectName("PaneHeader")
        root.addWidget(header)

        self._btn = QPushButton("⟳  Synchroniser Moodle")
        self._btn.setObjectName("Primary")
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setToolTip("Devoirs → agenda Apple et nouveaux documents Moodle (Ctrl+D)")
        self._btn.clicked.connect(lambda *_: self.sync_requested.emit())
        root.addWidget(self._btn)

        self._status = QLabel()
        self._status.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._table = MoodleTable()
        root.addWidget(self._table, 1)

    def set_busy(self, busy: bool, text: str = "") -> None:
        self._btn.setEnabled(not busy)
        self._btn.setText("⟳  Synchro en cours…" if busy else "⟳  Synchroniser Moodle")
        if text:
            self.set_status(text)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_data(self, a_faire: list[dict], documents: list[dict]) -> None:
        self._table.set_data(a_faire, documents)
