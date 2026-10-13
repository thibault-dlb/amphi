"""Résumé de fin de synchro Moodle : synthèse, erreurs, et le même tableau que la tuile."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..moodle.sync import SyncReport
from .moodle_table import MoodleTable
from .theme import COLORS


def _resume(report: SyncReport) -> str:
    compteurs = [
        ("Ajoutés", len(report.ajoutes)),
        ("Mis à jour", len(report.mis_a_jour)),
        ("Déplacés", len(report.deplaces)),
        ("Rendus ✅", len(report.rendus)),
        ("Supprimés", len(report.supprimes)),
    ]
    morceaux = [f"{nom} : {n}" for nom, n in compteurs if n]
    lignes = []
    if report.dry_run:
        lignes.append("[SIMULATION] Aucune modification écrite dans iCloud.")
    lignes.append(" · ".join(morceaux) if morceaux else "Agenda à jour.")
    if report.documents:
        lignes.append(
            f"Documents non synchronisés : {len(report.documents)} — lance /maj-moodle dans Claude Code."
        )
    return "\n".join(lignes)


def _ligne(texte: str, couleur: str) -> QLabel:
    label = QLabel(texte)
    label.setWordWrap(True)
    label.setStyleSheet(f"color:{couleur};")
    return label


class MoodleReportDialog(QDialog):
    def __init__(self, report: SyncReport, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Devoirs Moodle")
        self.setModal(True)
        self.setMinimumWidth(640)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        resume = QLabel(_resume(report))
        resume.setWordWrap(True)
        root.addWidget(resume)

        for erreur in report.erreurs:
            root.addWidget(_ligne(f"✗ {erreur}", COLORS["rec"]))
        for avertissement in report.avertissements:
            root.addWidget(_ligne(f"⚠ {avertissement}", COLORS["amber"]))

        table = MoodleTable()
        table.set_data(report.a_faire, report.documents)
        table.setMinimumHeight(260)
        root.addWidget(table, 1)

        bas = QHBoxLayout()
        bas.addStretch(1)
        ok = QPushButton("OK")
        ok.setObjectName("Primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        bas.addWidget(ok)
        root.addLayout(bas)
