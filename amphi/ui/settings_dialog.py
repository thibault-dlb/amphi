"""Réglages : rangement, micro & moteur, résumé Gemini, comportement."""

from __future__ import annotations

import sys

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..library import scan
from ..paths import APP_DIR
from .async_task import run_async
from .theme import COLORS

_AUDIO_FORMATS = [
    ("flac", "FLAC — sans perte (~55 Mo/h)"),
    ("wav", "WAV brut (~340 Mo/h)"),
    ("opus", "Opus — voix compressée (~15 Mo/h)"),
    ("none", "Ne pas garder l'audio"),
]
_COMPUTE_TYPES = ["int8", "int8_float32", "float32"]
_MODELS = [
    ("large-v3-turbo", "large-v3-turbo — recommandé (rapide, précision quasi identique)"),
    ("large-v3", "large-v3 — précision maximale, ~2× plus lent"),
    ("large-v2", "large-v2 — alternative, parfois moins d'hallucinations"),
    ("medium", "medium — léger, précision en retrait"),
]


class SettingsDialog(QDialog):
    applied = Signal()

    def __init__(self, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Réglages — Amphi")
        self.setModal(True)
        self.setMinimumSize(560, 520)

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._tab_storage(), "Rangement")
        tabs.addTab(self._tab_engine(), "Micro & moteur")
        tabs.addTab(self._tab_gemini(), "Résumé IA")
        tabs.addTab(self._tab_behaviour(), "Comportement")
        root.addWidget(tabs)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    # -- Rangement -----------------------------------------------------
    def _tab_storage(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)

        self._courses_root = QLineEdit(self.cfg.courses_root)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self._pick_courses_root)
        row = QHBoxLayout()
        row.addWidget(self._courses_root, 1)
        row.addWidget(browse)
        form.addRow("Dossier des cours", self._wrap(row))

        self._niveau = QComboBox()
        self._niveau.setEditable(True)
        self._reload_levels()
        self._niveau.setCurrentText(self.cfg.niveau)
        rescan = QPushButton("Scanner les matières de ce niveau")
        rescan.clicked.connect(self._rescan_subjects)
        form.addRow("Niveau", self._niveau)
        form.addRow("", rescan)

        self._matieres = QListWidget()
        self._matieres.addItems(self.cfg.matieres)
        add = QPushButton("Ajouter…")
        rem = QPushButton("Retirer")
        add.clicked.connect(self._add_subject)
        rem.clicked.connect(self._remove_subject)
        btns = QHBoxLayout()
        btns.addWidget(add)
        btns.addWidget(rem)
        btns.addStretch(1)
        form.addRow("Matières", self._matieres)
        form.addRow("", self._wrap(btns))

        self._audio_fmt = QComboBox()
        for key, label in _AUDIO_FORMATS:
            self._audio_fmt.addItem(label, key)
        keys = [k for k, _ in _AUDIO_FORMATS]
        self._audio_fmt.setCurrentIndex(
            keys.index(self.cfg.keep_audio_format) if self.cfg.keep_audio_format in keys else 0
        )
        form.addRow("Audio conservé", self._audio_fmt)
        return w

    # -- Micro & moteur ---------------------------------------------
    def _tab_engine(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)

        self._mic = QComboBox()
        self._mic.addItem("Auto (micro intégré)", None)
        try:
            from ..audio.devices import list_input_devices

            for d in list_input_devices():
                self._mic.addItem(f"{d.name} · {d.hostapi}", d.index)
        except Exception as exc:  # noqa: BLE001
            self._mic.addItem(f"(énumération impossible : {exc})", None)
        if self.cfg.mic_device_id is not None:
            i = self._mic.findData(self.cfg.mic_device_id)
            if i >= 0:
                self._mic.setCurrentIndex(i)
        form.addRow("Micro", self._mic)

        self._gain = QSpinBox()
        self._gain.setRange(0, 24)
        self._gain.setSingleStep(3)
        self._gain.setSuffix(" dB")
        self._gain.setValue(int(round(self.cfg.mic_gain_db)))
        form.addRow("Gain micro (capture)", self._gain)

        self._normalize = QCheckBox("Normaliser la parole avant transcription (recommandé)")
        self._normalize.setChecked(self.cfg.engine.normalize_speech)
        form.addRow("", self._normalize)
        ghint = QLabel(
            "Le micro intégré est peu sensible pour un prof à distance. Le gain amplifie "
            "le signal capté (le VU-mètre montre le résultat ; +6 à +12 dB en amphi). "
            "La normalisation remonte la parole juste pour Whisper, sans toucher l'audio archivé."
        )
        ghint.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        ghint.setWordWrap(True)
        form.addRow("", ghint)

        self._model = QComboBox()
        for value, label in _MODELS:
            self._model.addItem(label, value)
        mkeys = [v for v, _ in _MODELS]
        self._model.setCurrentIndex(
            mkeys.index(self.cfg.engine.model) if self.cfg.engine.model in mkeys else 0
        )
        form.addRow("Modèle", self._model)
        mhint = QLabel(
            "Mesuré sur ce PC : turbo ≈ 1,3× la durée du cours, large-v3 ≈ 2,5×. "
            "Sur du cours d'ingénierie la transcription est identique — large-v3 ne "
            "reprend l'avantage que sur audio difficile (bruit, accents, voix qui se chevauchent)."
        )
        mhint.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        mhint.setWordWrap(True)
        form.addRow("", mhint)

        self._compute = QComboBox()
        self._compute.addItems(_COMPUTE_TYPES)
        if self.cfg.engine.compute_type in _COMPUTE_TYPES:
            self._compute.setCurrentText(self.cfg.engine.compute_type)
        form.addRow("Quantification", self._compute)

        self._threads = QSpinBox()
        self._threads.setRange(1, 16)
        self._threads.setValue(self.cfg.engine.cpu_threads)
        form.addRow("Threads CPU", self._threads)

        self._beam = QSpinBox()
        self._beam.setRange(1, 10)
        self._beam.setValue(self.cfg.engine.beam_size)
        form.addRow("Beam size", self._beam)

        bench = QPushButton("Lancer un benchmark sur cette machine")
        bench.clicked.connect(self._run_benchmark)
        form.addRow("", bench)
        self._bench_notes = QLabel(self.cfg.engine.benchmark_notes or "Jamais mesuré.")
        self._bench_notes.setWordWrap(True)
        self._bench_notes.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        form.addRow("", self._bench_notes)

        self._prompt_matiere = QComboBox()
        self._prompt_matiere.addItems(self.cfg.matieres)
        self._prompt_text = QLineEdit()
        self._prompt_matiere.currentTextChanged.connect(self._load_matiere_prompt)
        self._load_matiere_prompt(self._prompt_matiere.currentText())
        form.addRow("Lexique Whisper — matière", self._prompt_matiere)
        form.addRow("Termes techniques", self._prompt_text)
        hint = QLabel("Termes récurrents à souffler au modèle (« Navier-Stokes, eigenvalue, MCO »).")
        hint.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        hint.setWordWrap(True)
        form.addRow("", hint)
        return w

    # -- Résumé IA -------------------------------------------------
    def _tab_gemini(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)

        self._g_enabled = QCheckBox("Activer le résumé automatique après transcription")
        self._g_enabled.setChecked(self.cfg.gemini.enabled)
        form.addRow(self._g_enabled)

        self._g_key = QLineEdit(self.cfg.gemini.api_key)
        self._g_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._g_key.setPlaceholderText("Clé API Google AI Studio (AIza…)")
        paste = QPushButton("Coller")
        test = QPushButton("Tester")
        paste.clicked.connect(lambda: self._g_key.setText(self._clipboard()))
        test.clicked.connect(self._test_key)
        krow = QHBoxLayout()
        krow.addWidget(self._g_key, 1)
        krow.addWidget(paste)
        krow.addWidget(test)
        form.addRow("Clé API", self._wrap(krow))

        self._g_status = QLabel("")
        self._g_status.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        form.addRow("", self._g_status)

        self._g_model = QComboBox()
        self._g_model.setEditable(True)
        self._g_model.addItems(
            ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.5-flash",
             "gemini-2.0-flash", self.cfg.gemini.model]
        )
        self._g_model.setCurrentText(self.cfg.gemini.model)
        refresh = QPushButton("Rafraîchir")
        refresh.clicked.connect(self._refresh_models)
        mrow = QHBoxLayout()
        mrow.addWidget(self._g_model, 1)
        mrow.addWidget(refresh)
        form.addRow("Modèle", self._wrap(mrow))

        self._g_auto = QCheckBox("Enchaîner automatiquement (sinon : bouton dans la file)")
        self._g_auto.setChecked(self.cfg.gemini.auto_after_transcription)
        form.addRow(self._g_auto)

        self._g_index = QCheckBox("Construire l'index des thèmes de chaque matière (2ᵉ requête Gemini)")
        self._g_index.setChecked(self.cfg.gemini.index_themes)
        form.addRow(self._g_index)
        index_now = QPushButton("Indexer les cours déjà transcrits…")
        index_now.clicked.connect(self._run_index_backfill)
        form.addRow("", index_now)
        ihint = QLabel(
            "Après chaque résumé, le fichier INDEX.md de la matière liste les thèmes vus et les "
            "lignes des transcripts où ils sont traités (nature du passage + note), pour Claude "
            "ou toi. Le bouton rattrape les cours transcrits avant (clé enregistrée requise)."
        )
        ihint.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        ihint.setWordWrap(True)
        form.addRow("", ihint)

        self._g_think = QSpinBox()
        self._g_think.setRange(-1, 24576)
        self._g_think.setSingleStep(1024)
        self._g_think.setValue(self.cfg.gemini.thinking_budget)
        self._g_think.setSpecialValueText("dynamique (-1)")
        form.addRow("Budget de réflexion", self._g_think)

        mnote = QLabel(
            "gemini-flash-latest suit le Flash stable du moment. Si un modèle est saturé "
            "(erreur 503), l'app bascule seule sur gemini-3.5-flash puis gemini-2.0-flash."
        )
        mnote.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        mnote.setWordWrap(True)
        form.addRow("", mnote)

        link = QLabel(
            'Clé gratuite : <a style="color:#4f8ff7" '
            'href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</a>'
            "  ·  <b>l'abonnement Google AI Plus ne donne pas de clé.</b>"
        )
        link.setOpenExternalLinks(True)
        link.setWordWrap(True)
        link.setStyleSheet("font-size:11px;")
        form.addRow(link)

        warn = QLabel(
            "⚠ Le transcript est envoyé aux serveurs Google. Sur le palier gratuit, "
            "Google peut l'utiliser pour améliorer ses produits."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color:{COLORS['amber']};font-size:11px;")
        form.addRow(warn)

        self._g_prompt = QPlainTextEdit(self.cfg.gemini.prompt_override)
        self._g_prompt.setPlaceholderText(
            "Vide = prompt par défaut (plan + synthèse + définitions/formules, orienté ingénierie)."
        )
        self._g_prompt.setFixedHeight(80)
        form.addRow("Prompt système (option)", self._g_prompt)
        return w

    # -- Comportement -----------------------------------------------
    def _tab_behaviour(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)

        self._ac_only = QCheckBox("Transcrire seulement quand le laptop est sur secteur")
        self._ac_only.setChecked(self.cfg.ac_only)
        form.addRow(self._ac_only)

        self._prevent_sleep = QCheckBox("Empêcher la veille pendant l'enregistrement")
        self._prevent_sleep.setChecked(self.cfg.prevent_sleep)
        form.addRow(self._prevent_sleep)

        self._twr = QCheckBox("Continuer la transcription pendant l'enregistrement")
        self._twr.setChecked(self.cfg.transcribe_while_recording)
        form.addRow(self._twr)
        twr_note = QLabel(
            "Décoché : la file se met en pause quand tu enregistres (reprise au checkpoint "
            "à l'arrêt). Coché : les deux tournent en parallèle — sur ce PC, quelques "
            "bribes d'audio peuvent être perdues si le CPU sature ; une alerte le signale."
        )
        twr_note.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        twr_note.setWordWrap(True)
        form.addRow("", twr_note)

        self._on_top = QCheckBox("Garder la fenêtre au-dessus des autres")
        self._on_top.setChecked(self.cfg.always_on_top)
        form.addRow(self._on_top)


        self._def_lang = QComboBox()
        self._def_lang.addItem("Français", "fr")
        self._def_lang.addItem("Anglais", "en")
        self._def_lang.setCurrentIndex(0 if self.cfg.default_langue != "en" else 1)
        form.addRow("Langue par défaut", self._def_lang)
        return w

    # -- helpers UI ----------------------------------------------------
    @staticmethod
    def _wrap(layout) -> QWidget:
        c = QWidget()
        c.setLayout(layout)
        return c

    @staticmethod
    def _clipboard() -> str:
        from PySide6.QtWidgets import QApplication

        return QApplication.clipboard().text().strip()

    def _pick_courses_root(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Dossier des cours", self._courses_root.text())
        if d:
            self._courses_root.setText(d)
            self._reload_levels()

    def _reload_levels(self) -> None:
        from pathlib import Path

        current = self._niveau.currentText()
        self._niveau.clear()
        try:
            self._niveau.addItems(scan.discover_levels(Path(self._courses_root.text())))
        except Exception:  # noqa: BLE001
            pass
        self._niveau.setCurrentText(current or self.cfg.niveau)

    def _rescan_subjects(self) -> None:
        from pathlib import Path

        found = scan.discover_subjects(Path(self._courses_root.text()), self._niveau.currentText())
        existing = [self._matieres.item(i).text() for i in range(self._matieres.count())]
        merged = scan.merge_subjects(existing, found)
        self._matieres.clear()
        self._matieres.addItems(merged)

    def _add_subject(self) -> None:
        name, ok = QInputDialog.getText(self, "Nouvelle matière", "Nom :")
        if ok and name.strip():
            self._matieres.addItem(name.strip())

    def _remove_subject(self) -> None:
        for item in self._matieres.selectedItems():
            self._matieres.takeItem(self._matieres.row(item))

    def _load_matiere_prompt(self, matiere: str) -> None:
        self._prompt_text.setText(self.cfg.matiere_prompts.get(matiere, ""))

    # -- benchmark ---------------------------------------------------
    def _run_benchmark(self) -> None:
        dlg = _ProcessDialog(
            "Benchmark",
            [sys.executable, "-u", str(APP_DIR / "tools" / "benchmark.py"), "--quick"],
            self,
        )
        dlg.exec()
        fresh = Config.load()
        self._compute.setCurrentText(fresh.engine.compute_type)
        self._threads.setValue(fresh.engine.cpu_threads)
        self._beam.setValue(fresh.engine.beam_size)
        self._bench_notes.setText(fresh.engine.benchmark_notes or "—")

    # -- index des thèmes ----------------------------------------------
    def _run_index_backfill(self) -> None:
        dlg = _ProcessDialog(
            "Index des thèmes",
            [sys.executable, "-u", "-m", "amphi.transcribe.themes", "--all"],
            self,
        )
        dlg.exec()

    # -- Gemini réseau ---------------------------------------------
    def _test_key(self) -> None:
        from ..config import GeminiConfig
        from ..transcribe.summarize import verify_key

        self._g_status.setText("Test en cours…")
        gcfg = GeminiConfig(api_key=self._g_key.text().strip(), model=self._g_model.currentText())

        def _done(res):
            ok, msg = res
            colour = COLORS["green"] if ok else COLORS["rec"]
            self._g_status.setStyleSheet(f"color:{colour};font-size:11px;")
            self._g_status.setText(("✓ " if ok else "✗ ") + msg)

        run_async(lambda: verify_key(gcfg), _done,
                  lambda e: self._g_status.setText(f"✗ {e}"), self)

    def _refresh_models(self) -> None:
        from ..transcribe.summarize import list_models

        key = self._g_key.text().strip()
        if not key:
            self._g_status.setText("Renseigne d'abord la clé.")
            return
        self._g_status.setText("Récupération des modèles…")

        def _done(names):
            self._g_status.setText(f"{len(names)} modèle(s) trouvé(s).")
            if names:
                keep = self._g_model.currentText()
                self._g_model.clear()
                self._g_model.addItems(names)
                self._g_model.setCurrentText(keep if keep in names else names[0])

        run_async(lambda: list_models(key), _done,
                  lambda e: self._g_status.setText(f"✗ {e}"), self)

    # -- sauvegarde -------------------------------------------------
    def _save(self) -> None:
        c = self.cfg
        c.courses_root = self._courses_root.text().strip()
        c.niveau = self._niveau.currentText().strip()
        c.matieres = [self._matieres.item(i).text() for i in range(self._matieres.count())]
        c.keep_audio_format = self._audio_fmt.currentData()

        c.mic_device_id = self._mic.currentData()
        c.mic_gain_db = float(self._gain.value())
        c.engine.model = self._model.currentData()
        c.engine.compute_type = self._compute.currentText()
        c.engine.cpu_threads = self._threads.value()
        c.engine.beam_size = self._beam.value()
        c.engine.normalize_speech = self._normalize.isChecked()
        matiere = self._prompt_matiere.currentText().strip()
        if matiere:
            text = self._prompt_text.text().strip()
            if text:
                c.matiere_prompts[matiere] = text
            else:
                c.matiere_prompts.pop(matiere, None)

        c.gemini.enabled = self._g_enabled.isChecked()
        c.gemini.api_key = self._g_key.text().strip()
        c.gemini.model = self._g_model.currentText().strip() or "gemini-flash-latest"
        c.gemini.auto_after_transcription = self._g_auto.isChecked()
        c.gemini.index_themes = self._g_index.isChecked()
        c.gemini.thinking_budget = self._g_think.value()
        c.gemini.prompt_override = self._g_prompt.toPlainText().strip()
        if c.gemini.api_key:
            c.gemini.acknowledged_privacy = True

        c.ac_only = self._ac_only.isChecked()
        c.prevent_sleep = self._prevent_sleep.isChecked()
        c.transcribe_while_recording = self._twr.isChecked()
        c.always_on_top = self._on_top.isChecked()
        c.default_langue = self._def_lang.currentData()

        c.onboarding_done = True
        c.save()
        self.applied.emit()
        self.accept()


class _ProcessDialog(QDialog):
    """Lance une commande et affiche sa sortie en direct (benchmark)."""

    def __init__(self, title: str, argv: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(640, 380)
        lay = QVBoxLayout(self)
        self._out = QPlainTextEdit(readOnly=True)
        self._out.setStyleSheet("font-family:Consolas,monospace;font-size:11px;")
        lay.addWidget(self._out)
        self._bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._bb.rejected.connect(self.reject)
        self._bb.button(QDialogButtonBox.StandardButton.Close).setEnabled(False)
        lay.addWidget(self._bb)

        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(str(APP_DIR))  # `python -m amphi…` doit trouver le paquet
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._read)
        self._proc.finished.connect(self._finished)
        self._proc.start(argv[0], argv[1:])

    def _read(self) -> None:
        self._out.appendPlainText(bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace").rstrip())

    def _finished(self, code: int, _status) -> None:
        self._out.appendPlainText(f"\n— terminé (code {code}) —")
        self._bb.button(QDialogButtonBox.StandardButton.Close).setEnabled(True)

    def closeEvent(self, event) -> None:
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            self._proc.kill()
        super().closeEvent(event)
