"""Dialogue de validation à l'arrêt d'un enregistrement : matière, titre, date, langue."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from ..audio.recorder import RecordingResult
from ..config import Config
from ..library import scan
from ..models import RecordingMeta
from ..paths import sanitize_component
from .theme import COLORS


class ValidateDialog(QDialog):
    """Accept -> .result_meta est renseigné. 'Plus tard' -> laisse en staging."""

    DELETE = 2  # code de retour custom
    RESUME = 3  # rouvrir l'enregistrement pour le poursuivre

    def __init__(self, cfg: Config, rec: RecordingResult, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.rec = rec
        self.result_meta: RecordingMeta | None = None
        self.setWindowTitle("Classer l'enregistrement")
        self.setModal(True)
        self.setMinimumWidth(430)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        summary = QLabel(
            f"Durée {RecordingMeta.hhmmss(rec.duration_s)}  ·  "
            f"{len(rec.markers)} marqueur(s)  ·  {rec.sample_rate // 1000} kHz"
        )
        summary.setStyleSheet(f"color:{COLORS['dim']};")
        root.addWidget(summary)

        form = QFormLayout()
        form.setSpacing(9)

        self._niveau = QComboBox()
        self._niveau.setEditable(True)
        self._niveau.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        try:
            levels = scan.discover_levels(cfg.courses_root_path())
        except Exception:  # noqa: BLE001
            levels = []
        if cfg.niveau and cfg.niveau not in levels:
            levels.append(cfg.niveau)
        self._niveau.addItems(levels)
        self._niveau.setCurrentText(cfg.niveau)
        form.addRow("Niveau", self._niveau)

        self._matiere = QComboBox()
        self._matiere.setEditable(True)
        self._matiere.addItems(cfg.matieres)
        self._matiere.setCurrentText("")
        self._matiere.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        form.addRow("Matière", self._matiere)

        self._titre = QLineEdit()
        self._titre.setPlaceholderText("Ex. Complexité amortie — chapitre 3")
        form.addRow("Titre", self._titre)

        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("yyyy-MM-dd")
        self._date.setDate(QDate.currentDate())
        form.addRow("Date", self._date)

        lang_row = QHBoxLayout()
        self._fr = QRadioButton("Français")
        self._en = QRadioButton("Anglais")
        (self._fr if cfg.default_langue != "en" else self._en).setChecked(True)
        lang_row.addWidget(self._fr)
        lang_row.addWidget(self._en)
        lang_row.addStretch(1)
        form.addRow("Langue principale", lang_row)

        root.addLayout(form)

        self._remember = QCheckBox("Ajouter cette matière à ma liste")
        self._remember.setChecked(True)
        self._remember.setVisible(False)
        root.addWidget(self._remember)

        self._remember_niveau = QCheckBox("Définir comme mon niveau par défaut")
        self._remember_niveau.setChecked(True)
        self._remember_niveau.setVisible(False)
        root.addWidget(self._remember_niveau)

        self._path = QLabel()
        self._path.setWordWrap(True)
        self._path.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        root.addWidget(self._path)

        buttons = QDialogButtonBox()
        self._ok = buttons.addButton("Classer et transcrire", QDialogButtonBox.ButtonRole.AcceptRole)
        self._ok.setObjectName("Primary")
        self._resume = QPushButton("↩  Reprendre l'enregistrement")
        self._resume.setToolTip("Annule l'arrêt : la capture continue dans le même fichier.")
        buttons.addButton(self._resume, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton("Plus tard", QDialogButtonBox.ButtonRole.RejectRole)
        self._del = QPushButton("Supprimer")
        self._del.setStyleSheet(f"color:{COLORS['rec']};")
        buttons.addButton(self._del, QDialogButtonBox.ButtonRole.DestructiveRole)
        root.addWidget(buttons)

        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        self._del.clicked.connect(lambda: self.done(self.DELETE))
        self._resume.clicked.connect(lambda: self.done(self.RESUME))

        self._niveau.currentTextChanged.connect(self._update_preview)
        self._niveau.currentTextChanged.connect(self._on_niveau_changed)
        for w in (self._matiere, self._titre):
            (w.currentTextChanged if isinstance(w, QComboBox) else w.textChanged).connect(
                self._update_preview
            )
        self._date.dateChanged.connect(self._update_preview)
        self._matiere.currentTextChanged.connect(self._on_matiere_changed)
        self._update_preview()
        (self._niveau if not cfg.niveau else self._matiere).setFocus()

    # ------------------------------------------------------------------
    def _current_date(self) -> str:
        return self._date.date().toString("yyyy-MM-dd")

    def _current_titre(self) -> str:
        typed = self._titre.text().strip()
        return typed or f"Cours {self._current_date()}"

    def _on_matiere_changed(self, text: str) -> None:
        known = text.strip() in self.cfg.matieres or not text.strip()
        self._remember.setVisible(not known)

    def _on_niveau_changed(self, text: str) -> None:
        text = text.strip()
        self._remember_niveau.setVisible(bool(text) and text != self.cfg.niveau)

    def _update_preview(self) -> None:
        matiere = self._matiere.currentText().strip() or "…"
        target = self.cfg.target_dir_for(
            sanitize_component(matiere, fallback="Divers"),
            self._current_date(),
            sanitize_component(self._current_titre()),
            niveau=self._niveau.currentText().strip(),
        )
        self._path.setText(f"→ {target}")

    def _on_accept(self) -> None:
        niveau = self._niveau.currentText().strip()
        matiere = self._matiere.currentText().strip()
        if not niveau:
            self._niveau.setFocus()
            self._path.setText("⚠ Choisis un niveau (ex. B3).")
            return
        if not matiere:
            self._matiere.setFocus()
            self._path.setText("⚠ Choisis une matière.")
            return

        dirty = False
        if niveau != self.cfg.niveau and self._remember_niveau.isChecked():
            self.cfg.niveau = niveau
            dirty = True
        matiere_new = matiere not in self.cfg.matieres
        if matiere_new and self._remember.isChecked():
            self.cfg.matieres = sorted({*self.cfg.matieres, matiere}, key=str.casefold)
            dirty = True
        if dirty:
            self.cfg.save()

        self.result_meta = RecordingMeta(
            titre=self._current_titre(),
            matiere=matiere,
            niveau=niveau,
            date=self._current_date(),
            langue="en" if self._en.isChecked() else "fr",
            duree_s=self.rec.duration_s,
            sample_rate=self.rec.sample_rate,
            markers=list(self.rec.markers),
        )
        self.accept()
