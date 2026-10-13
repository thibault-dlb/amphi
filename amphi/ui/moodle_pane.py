"""Tuile « MOODLE » (colonne de gauche) : synchro devoirs → agenda Apple, état et tableau."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout

from .moodle_table import MoodleTable
from .theme import COLORS


class MoodlePane(QFrame):
    sync_requested = Signal()
    fetch_requested = Signal()     # récupérer les documents via Claude Code (/maj-moodle)

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

        self._fetch = QPushButton("📥  Récupérer les documents (Claude)")
        self._fetch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fetch.setToolTip(
            "Ouvre une console avec Claude Code qui lance /maj-moodle : télécharge et range "
            "les documents non synchronisés. Amphi resynchronise quand la console est fermée."
        )
        self._fetch.clicked.connect(lambda *_: self.fetch_requested.emit())
        self._fetch.setEnabled(False)
        self._docs = 0
        self._fetch_running = False
        root.addWidget(self._fetch)

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
        self._docs = len(documents)
        self._refresh_fetch()

    def set_fetch_running(self, running: bool) -> None:
        """Claude Code (/maj-moodle) est ouvert dans une console."""
        self._fetch_running = running
        self._refresh_fetch()

    def _refresh_fetch(self) -> None:
        if self._fetch_running:
            self._fetch.setText("📥  Claude en cours…")
            self._fetch.setToolTip("Ferme la console Claude : Amphi resynchronise et met le tableau à jour.")
            self._fetch.setEnabled(False)
        else:
            n = f" ({self._docs})" if self._docs else ""
            self._fetch.setText(f"📥  Récupérer les documents{n} (Claude)")
            self._fetch.setEnabled(self._docs > 0)
