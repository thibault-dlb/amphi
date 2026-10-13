"""Réglages Moodle → agenda Apple : identifiants iCloud (mot de passe d'app) et test."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..config import Config
from .async_task import run_async
from .theme import COLORS


class MoodleDialog(QDialog):
    def __init__(self, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Agenda Apple — devoirs Moodle")
        self.setModal(True)
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        intro = QLabel(
            "Les devoirs du campus Paris sont copiés dans le calendrier iCloud « "
            f"{self.cfg.moodle.calendar_name} ». Il faut un mot de passe d'application Apple : "
            "crée-le sur account.apple.com → Connexion et sécurité → Mots de passe pour app. "
            "Ton mot de passe Apple habituel ne fonctionnera pas."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(10)

        stored = self._stored_credentials()
        self._apple_id = QLineEdit(stored[0] if stored else "")
        self._apple_id.setPlaceholderText("prenom.nom@icloud.com")
        form.addRow("Identifiant Apple", self._apple_id)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("xxxx-xxxx-xxxx-xxxx")
        form.addRow("Mot de passe d'app", self._password)

        pwd_hint = QLabel(
            "Laisse vide pour garder le mot de passe déjà enregistré."
            if stored
            else "Enregistré dans le trousseau Windows, jamais dans config.json."
        )
        pwd_hint.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        pwd_hint.setWordWrap(True)
        form.addRow("", pwd_hint)

        self._sync_startup = QCheckBox("Synchroniser au démarrage d'Amphi")
        self._sync_startup.setChecked(self.cfg.moodle.sync_on_startup)
        form.addRow("", self._sync_startup)
        root.addLayout(form)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color:{COLORS['dim']};font-size:11px;")
        root.addWidget(self._status)

        btns = QHBoxLayout()
        test = QPushButton("Tester")
        test.clicked.connect(self._test)
        forget = QPushButton("Oublier les identifiants")
        forget.clicked.connect(self._forget)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Enregistrer")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        btns.addWidget(test)
        btns.addWidget(forget)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(save)
        root.addLayout(btns)

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _stored_credentials():
        try:
            from ..moodle.secrets import get_credentials
        except ImportError:
            return None
        try:
            return get_credentials()
        except Exception:  # noqa: BLE001
            return None

    def _set_status(self, text: str, colour: str = "dim") -> None:
        self._status.setStyleSheet(f"color:{COLORS[colour]};font-size:11px;")
        self._status.setText(text)

    # -- actions ---------------------------------------------------------
    def _test(self) -> None:
        from ..moodle.icloud import test_connection

        apple_id = self._apple_id.text().strip()
        password = self._password.text().strip()
        if not password:
            stored = self._stored_credentials()
            if stored and stored[0] == apple_id:
                password = stored[1]
        if not apple_id or not password:
            self._set_status("✗ Renseigne l'identifiant et le mot de passe d'app.", "rec")
            return

        self._set_status("Test en cours…")
        calendar = self.cfg.moodle.calendar_name

        def _done(msg):
            self._set_status(f"✓ {msg}", "green")

        def _failed(err):
            self._set_status(f"✗ {err}", "rec")

        run_async(lambda: test_connection(apple_id, password, calendar), _done, _failed, self)

    def _save(self) -> None:
        from ..moodle.secrets import get_credentials, set_credentials

        apple_id = self._apple_id.text().strip()
        password = self._password.text().strip()
        if not apple_id:
            self._set_status("✗ Renseigne l'identifiant Apple.", "rec")
            return
        stored = self._stored_credentials()
        if not password and not (stored and stored[0] == apple_id):
            self._set_status("✗ Renseigne le mot de passe d'app.", "rec")
            return
        try:
            if password:
                set_credentials(apple_id, password)
            elif get_credentials() is None:
                self._set_status("✗ Renseigne le mot de passe d'app.", "rec")
                return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Agenda Apple", f"Enregistrement impossible : {exc}")
            return

        self.cfg.moodle.enabled = True
        self.cfg.moodle.sync_on_startup = self._sync_startup.isChecked()
        self.cfg.save()
        self.accept()

    def _forget(self) -> None:
        from ..moodle.secrets import clear_credentials

        try:
            clear_credentials()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Agenda Apple", f"Suppression impossible : {exc}")
            return
        self.cfg.moodle.enabled = False
        self.cfg.save()
        self._apple_id.clear()
        self._password.clear()
        self._set_status("Identifiants oubliés. La synchronisation est désactivée.")
