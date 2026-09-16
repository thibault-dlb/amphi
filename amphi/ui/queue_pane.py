"""Colonne droite : file d'attente transcription/résumé + état de l'alimentation."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import power
from ..library.store import Recording, Store
from ..models import RecordingMeta, Status
from ..paths import FORCE_BATTERY_FLAG
from .theme import COLORS


def _structural_sig(rows: list[Recording]) -> tuple:
    """Change quand la liste ou les statuts changent (pas à chaque % de progression)."""
    return tuple((r.id, r.status) for r in rows)


def _elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60} min {seconds % 60:02d} s"


def _gemini_label(what: str, r: Recording) -> str:
    """« Résumé Gemini — gemini-flash-latest · essai 1/4 · … · en attente de la réponse… · 42 s ».

    Un détail qui finit par « … » est une attente ouverte depuis la dernière écriture
    du worker (updated_at) : on y ajoute le temps écoulé."""
    if not r.stage:
        return f"{what} en cours…"
    text = f"{what} — {r.stage}"
    if r.stage.endswith("…") and r.updated_at:
        text += f" · {_elapsed(time.time() - r.updated_at)}"
    return text


class QueueItem(QFrame):
    open_folder = Signal(str)
    retry = Signal(int)
    retry_summary = Signal(int)
    remove = Signal(int)
    pause = Signal(int)
    resume = Signal(int)

    def __init__(self, rec: Recording, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.rec = rec
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        title = QLabel(f"{rec.matiere} — {rec.titre}")
        title.setStyleSheet("font-weight:600;")
        title.setWordWrap(True)
        lay.addWidget(title)

        meta = QLabel(f"{rec.date} · {RecordingMeta.hhmmss(rec.duree_s)}")
        meta.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        lay.addWidget(meta)

        self._status = QLabel()
        self._status.setStyleSheet("font-size:12px;")
        self._status.setWordWrap(True)  # détail Gemini : modèle, essai, attente
        lay.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        lay.addWidget(self._bar)

        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(6)
        lay.addLayout(self._buttons)

        self._render()

    def _btn(self, text: str, slot) -> None:
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        self._buttons.addWidget(b)

    def _clear_buttons(self) -> None:
        while self._buttons.count():
            item = self._buttons.takeAt(0)  # retire aussi les stretches
            if item.widget():
                item.widget().deleteLater()

    def _render(self) -> None:
        r = self.rec
        colour = {
            Status.DONE: COLORS["green"],
            Status.TRANSCRIBE_FAILED: COLORS["rec"],
            Status.SUMMARY_FAILED: COLORS["amber"],
            Status.INDEX_FAILED: COLORS["amber"],
            Status.PAUSED_NO_AC: COLORS["amber"],
            Status.PAUSED_USER: COLORS["amber"],
        }.get(r.status, COLORS["text"])

        label = {
            Status.QUEUED: "En attente de transcription",
            Status.TRANSCRIBING: r.stage or "Transcription…",
            Status.PAUSED_NO_AC: "En pause — laptop sur batterie",
            Status.PAUSED_USER: (
                f"En pause — {r.stage}" if r.stage else "En pause"
            ),
            Status.TRANSCRIBED: "Transcript prêt — résumé à suivre",
            Status.SUMMARIZING: _gemini_label("Résumé Gemini", r),
            Status.INDEXING: _gemini_label("Index des thèmes", r),
            Status.DONE: "✓ Terminé",
            Status.TRANSCRIBE_FAILED: "✗ Échec de la transcription",
            Status.SUMMARY_FAILED: "✓ Transcript OK · résumé en échec",
            Status.INDEX_FAILED: "✓ Transcript + résumé OK · index en échec",
        }.get(r.status, r.status)
        self._status.setText(label)
        self._status.setStyleSheet(f"font-size:12px;color:{colour};")
        if r.error:
            self._status.setToolTip(r.error)

        if r.status in (Status.TRANSCRIBING, Status.PAUSED_USER) and r.progress > 0:
            self._bar.setRange(0, 100)
            self._bar.setValue(int(r.progress * 100))
            self._bar.show()
        elif r.status in (Status.SUMMARIZING, Status.INDEXING, Status.QUEUED):
            self._bar.setRange(0, 0)  # indéterminé
            self._bar.show()
        else:
            self._bar.hide()

        self._clear_buttons()
        deliverable = (Status.DONE, Status.TRANSCRIBED, Status.SUMMARY_FAILED, Status.INDEX_FAILED)
        if r.status in deliverable and r.target_dir:
            self._btn("Ouvrir", lambda: self.open_folder.emit(r.target_dir))
        if r.status == Status.SUMMARY_FAILED:
            self._btn("Relancer le résumé", lambda: self.retry_summary.emit(r.id))
        if r.status == Status.INDEX_FAILED:
            # même relance : resume.md existe déjà, seul l'index est refait
            self._btn("Relancer l'index", lambda: self.retry_summary.emit(r.id))
        if r.status == Status.TRANSCRIBE_FAILED:
            self._btn("Réessayer", lambda: self.retry.emit(r.id))
            self._btn("Retirer", lambda: self.remove.emit(r.id))
        if r.status in Status.PAUSABLE:
            self._btn("⏸  Pause", lambda: self.pause.emit(r.id))
        elif r.status == Status.PAUSED_USER:
            self._btn("▶  Reprendre", lambda: self.resume.emit(r.id))
        self._buttons.addStretch(1)

    def update_from(self, rec: Recording) -> None:
        self.rec = rec
        self._render()


class QueuePane(QFrame):
    open_folder_requested = Signal(str)
    retry_requested = Signal(int)
    retry_summary_requested = Signal(int)
    remove_requested = Signal(int)
    pause_requested = Signal(int)
    resume_requested = Signal(int)
    work_available = Signal()   # il y a quelque chose à traiter -> (re)lancer le worker
    force_battery_toggled = Signal(bool)

    def __init__(self, store: Store, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.store = store
        self._items: dict[int, QueueItem] = {}
        self._last_sig: tuple = ()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        head = QHBoxLayout()
        header = QLabel("FILE D'ATTENTE")
        header.setObjectName("PaneHeader")
        head.addWidget(header)
        head.addStretch(1)
        self._power = QLabel("—")
        self._power.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        head.addWidget(self._power)
        root.addLayout(head)

        self._force_btn = QPushButton()
        self._force_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._force_btn.setVisible(False)
        self._force_btn.clicked.connect(self._on_force_clicked)
        root.addWidget(self._force_btn)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._holder = QWidget()
        self._list = QVBoxLayout(self._holder)
        self._list.setContentsMargins(0, 0, 4, 0)
        self._list.setSpacing(8)
        self._list.addStretch(1)
        self._scroll.setWidget(self._holder)
        root.addWidget(self._scroll, 1)

        self._empty = QLabel("Rien en attente.\nLes enregistrements validés arrivent ici.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(f"color:{COLORS['dim']};font-size:12px;")
        self._list.insertWidget(0, self._empty)

        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    # ------------------------------------------------------------------
    def _on_force_clicked(self) -> None:
        self.force_battery_toggled.emit(not FORCE_BATTERY_FLAG.exists())
        self.refresh()

    def refresh(self) -> None:
        rows = self.store.all(limit=40)
        actionable = any(r.status in Status.ACTIONABLE for r in rows)
        self._update_power(actionable)

        sig = _structural_sig(rows)
        if sig != self._last_sig:
            self._last_sig = sig
            self._rebuild(rows)
        else:
            for rec in rows:  # mise à jour légère (progression, texte d'étape)
                item = self._items.get(rec.id)
                if item is not None:
                    item.update_from(rec)
        if actionable and (power.on_ac_power() or FORCE_BATTERY_FLAG.exists()):
            self.work_available.emit()

    def _update_power(self, actionable: bool = False) -> None:
        on_ac = power.on_ac_power()
        forced = FORCE_BATTERY_FLAG.exists()

        self._force_btn.setVisible(not on_ac and actionable)
        if not on_ac:
            self._force_btn.setText(
                "⏸  Repasser en attente du secteur" if forced
                else "⚡  Transcrire maintenant (batterie)"
            )
            self._force_btn.setObjectName("" if forced else "Primary")
            self._force_btn.style().unpolish(self._force_btn)
            self._force_btn.style().polish(self._force_btn)

        pct = power.battery_percent()
        if on_ac:
            self._power.setText("🔌 secteur")
            self._power.setStyleSheet(f"color:{COLORS['green']};font-size:11px;")
        elif forced:
            txt = "🔋 batterie" + (f" {pct}%" if pct is not None else "")
            self._power.setText(txt + " — transcription forcée")
            self._power.setStyleSheet(f"color:{COLORS['accent']};font-size:11px;")
        else:
            txt = "🔋 batterie" + (f" {pct}%" if pct is not None else "")
            self._power.setText(txt + " — transcription en pause")
            self._power.setStyleSheet(f"color:{COLORS['amber']};font-size:11px;")

    def _rebuild(self, rows: list[Recording]) -> None:
        self._empty.setVisible(not rows)
        # on vide et on reconstruit : ne tourne que sur changement de structure
        while self._list.count():
            item = self._list.takeAt(0)
            if item.widget() and item.widget() is not self._empty:
                item.widget().deleteLater()
        self._items.clear()

        self._list.addWidget(self._empty)
        for rec in rows:
            w = QueueItem(rec)
            w.open_folder.connect(self.open_folder_requested)
            w.retry.connect(self.retry_requested)
            w.retry_summary.connect(self.retry_summary_requested)
            w.remove.connect(self.remove_requested)
            w.pause.connect(self.pause_requested)
            w.resume.connect(self.resume_requested)
            self._items[rec.id] = w
            self._list.addWidget(w)
        self._list.addStretch(1)
