"""Tableau « Devoirs | Documents non synchronisés » (tuile Moodle et résumé de synchro)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from ..moodle.sync import ligne_devoir, ligne_document
from .theme import COLORS

_COULEUR_ETAT = {
    "en retard": COLORS["amber"],
    "fermé": COLORS["rec"],
    "sans date": COLORS["dim"],
    "rendu": COLORS["green"],
}
_URL = Qt.ItemDataRole.UserRole


class MoodleTable(QTableWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["Devoirs", "Documents\nnon synchronisés"])
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.horizontalHeader().setSectionsClickable(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setShowGrid(False)
        self.setWordWrap(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # les retours à la ligne dépendent de la largeur des colonnes (stretch appliqué après coup)
        self.horizontalHeader().sectionResized.connect(lambda *_: self.resizeRowsToContents())
        self.itemDoubleClicked.connect(self._ouvrir)

    def set_data(self, a_faire: list[dict], documents: list[dict]) -> None:
        self.clearContents()
        self.setRowCount(max(len(a_faire), len(documents), 1))

        if not a_faire:
            self.setItem(0, 0, _cellule_vide("Aucun devoir"))
        for i, d in enumerate(a_faire):
            item = QTableWidgetItem(ligne_devoir(d))
            url = d.get("url") or ""
            if url:
                item.setToolTip(url)
                item.setData(_URL, url)
            couleur = _COULEUR_ETAT.get(d.get("etat", ""))
            if couleur:
                item.setForeground(QColor(couleur))
            self.setItem(i, 0, item)

        if not documents:
            self.setItem(0, 1, _cellule_vide("Tout est synchronisé"))
        for i, d in enumerate(documents):
            self.setItem(i, 1, QTableWidgetItem(ligne_document(d)))

        self.resizeRowsToContents()

    def _ouvrir(self, item: QTableWidgetItem) -> None:
        url = item.data(_URL)
        if url:
            QDesktopServices.openUrl(QUrl(url))


def _cellule_vide(texte: str) -> QTableWidgetItem:
    item = QTableWidgetItem(texte)
    item.setForeground(QColor(COLORS["dim"]))
    font = item.font()
    font.setItalic(True)
    item.setFont(font)
    return item
