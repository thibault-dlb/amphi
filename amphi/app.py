"""Point d'entrée de l'interface Amphi."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from .config import Config
from .library.store import Store
from .logging_setup import setup
from .paths import ASSETS_DIR, ensure_dirs
from .ui.main_window import MainWindow
from .ui.theme import QSS

log = logging.getLogger("amphi")
_SINGLE_KEY = "amphi-single-instance-v1"
APP_ID = "Amphi.CourseRecorder"  # AppUserModelID — détache la barre des tâches de pythonw.exe


def _set_app_user_model_id() -> None:
    """Sans ça, Windows regroupe l'app sous pythonw.exe et en montre l'icône."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # noqa: BLE001
        log.debug("SetCurrentProcessExplicitAppUserModelID", exc_info=True)


def _acquire_single_instance() -> QLocalServer | None:
    probe = QLocalSocket()
    probe.connectToServer(_SINGLE_KEY)
    if probe.waitForConnected(250):
        probe.write(b"show")
        probe.flush()
        probe.waitForBytesWritten(250)
        probe.disconnectFromServer()
        return None
    QLocalServer.removeServer(_SINGLE_KEY)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    return server if server.listen(_SINGLE_KEY) else None


def main() -> int:
    ensure_dirs()
    setup("amphi", console=True)
    _set_app_user_model_id()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Amphi")
    app.setApplicationDisplayName("Amphi")
    app.setQuitOnLastWindowClosed(False)

    icon_path = ASSETS_DIR / "amphi.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    server = _acquire_single_instance()
    if server is None:
        log.info("Une instance d'Amphi est déjà ouverte — sortie.")
        return 0

    app.setStyleSheet(QSS)

    from . import __version__
    from .ui.screens import describe

    log.info("Amphi %s — démarrage.", __version__)
    for line in describe():
        log.info("Écran : %s", line)
    cfg = Config.load()
    store = Store()
    window = MainWindow(cfg, store)

    def _on_new_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.waitForReadyRead(200)
            conn.readAll()
            window._reveal()
            conn.disconnectFromServer()

    server.newConnection.connect(_on_new_connection)

    try:
        return app.exec()
    finally:
        server.close()
        store.close()
        log.info("Amphi fermé.")


if __name__ == "__main__":
    sys.exit(main())
