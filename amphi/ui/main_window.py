"""Fenêtre principale — fenêtre Windows classique (redimensionnable, Win+flèches,
minimisation, menus). Colonne enregistrement + colonne file d'attente, séparées par
une poignée déplaçable. La transcription tourne dans un worker séparé.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil
from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import power
from ..audio.recorder import CAPTURE_WAV, SIDECAR, RecorderState, RecordingResult
from ..config import Config
from ..library.store import Store
from ..models import Marker, RecordingMeta, Status
from ..paths import (
    APP_DIR,
    ASSETS_DIR,
    FORCE_BATTERY_FLAG,
    RECORDING_FLAG,
    STAGING_DIR,
    WORKER_LOCK,
    WORKER_STOP_FLAG,
)
from .queue_pane import QueuePane
from .recorder_pane import RecorderPane
from .screens import snap_to_screenpad
from .settings_dialog import SettingsDialog
from .validate_dialog import ValidateDialog

log = logging.getLogger(__name__)

_DETACHED = 0x00000008 | 0x08000000 | 0x00000200  # DETACHED | NO_WINDOW | NEW_GROUP
_STACK_BELOW_WIDTH = 640  # en dessous : les deux panneaux s'empilent verticalement


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, store: Store) -> None:
        super().__init__()
        self.cfg = cfg
        self.store = store
        self._quitting = False
        self._last_spawn = 0.0
        self._sleep_held = False
        self._glitch_seen = False

        self.setWindowTitle("Amphi")
        self.setMinimumSize(440, 400)
        icon = ASSETS_DIR / "amphi.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        RECORDING_FLAG.unlink(missing_ok=True)  # nettoie un drapeau resté après un crash
        FORCE_BATTERY_FLAG.unlink(missing_ok=True)  # option de session, jamais persistée

        self._build_menus()
        self._build_central()
        self._wire()
        self._build_tray()
        self._apply_always_on_top()
        self._restore_window()

        QTimer.singleShot(400, self._recover_orphans)
        self._housekeeping = QTimer(self)
        self._housekeeping.setInterval(5000)
        self._housekeeping.timeout.connect(self._tick)
        self._housekeeping.start()
        if self.store.pending_exists():
            self.ensure_worker()

    # ------------------------------------------------------------------ UI
    def _build_menus(self) -> None:
        m_file = self.menuBar().addMenu("&Fichier")
        act_settings = QAction("&Réglages…", self)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        act_settings.triggered.connect(self._open_settings)
        m_file.addAction(act_settings)
        m_file.addSeparator()
        act_quit = QAction("&Quitter", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_view = self.menuBar().addMenu("&Affichage")
        self._act_ontop = QAction("Toujours au-&dessus", self, checkable=True)
        self._act_ontop.setChecked(self.cfg.always_on_top)
        self._act_ontop.toggled.connect(self._set_pinned)
        m_view.addAction(self._act_ontop)
        act_recenter = QAction("Ancrer sur le &ScreenPad", self)
        act_recenter.setShortcut(QKeySequence("Ctrl+Home"))
        act_recenter.triggered.connect(self._snap_screenpad)
        m_view.addAction(act_recenter)

    def _snap_screenpad(self) -> None:
        snap_to_screenpad(self, self.cfg.screenpad_side)

    def _build_central(self) -> None:
        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(6)

        self._banner = self._make_banner()
        v.addWidget(self._banner)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(6)
        self.recorder_pane = RecorderPane(self.cfg)
        self.queue_pane = QueuePane(self.store)
        self._splitter.addWidget(self.recorder_pane)
        self._splitter.addWidget(self.queue_pane)
        self._splitter.setStretchFactor(0, 5)
        self._splitter.setStretchFactor(1, 4)
        v.addWidget(self._splitter, 1)

        self.setCentralWidget(central)
        self._refresh_banner()

    def _make_banner(self) -> QFrame:
        f = QFrame()
        lay = QHBoxLayout(f)
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(8)
        self._banner_text = QLabel()
        self._banner_text.setObjectName("Banner")
        self._banner_text.setWordWrap(True)
        lay.addWidget(self._banner_text, 1)
        act = QPushButton("Configurer")
        act.setObjectName("Primary")
        act.clicked.connect(self._open_settings)
        lay.addWidget(act)
        dismiss = QPushButton("×")
        dismiss.setObjectName("IconBtn")
        dismiss.clicked.connect(self._dismiss_banner)
        lay.addWidget(dismiss)
        return f

    def _refresh_banner(self) -> None:
        problems = []
        if not self.cfg.niveau:
            problems.append("ton niveau")
        if not self.cfg.matieres:
            problems.append("tes matières")
        if self.cfg.gemini.enabled and not self.cfg.gemini.api_key:
            problems.append("ta clé Gemini (résumés)")
        show = bool(problems) and not self.cfg.onboarding_done
        self._banner.setVisible(show)
        if show:
            self._banner_text.setText("À configurer : " + ", ".join(problems) + ".")

    def _dismiss_banner(self) -> None:
        self.cfg.onboarding_done = True
        self.cfg.save()
        self._banner.setVisible(False)

    def _wire(self) -> None:
        self.recorder_pane.recording_started.connect(self._on_recording_started)
        self.recorder_pane.state_changed.connect(self._on_recorder_state)
        self.recorder_pane.recording_finished.connect(self._on_recording_finished)
        self.recorder_pane.silence_alert.connect(self._on_silence_alert)
        self.recorder_pane.glitch_alert.connect(self._on_glitch_alert)

        self.queue_pane.open_folder_requested.connect(self._open_folder)
        self.queue_pane.retry_requested.connect(self._retry)
        self.queue_pane.retry_summary_requested.connect(self._retry_summary)
        self.queue_pane.remove_requested.connect(self._remove)
        self.queue_pane.work_available.connect(self.ensure_worker)
        self.queue_pane.force_battery_toggled.connect(self.set_force_battery)

    # -------------------------------------------------------------- fenêtre
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        want = (
            Qt.Orientation.Vertical
            if self.width() < _STACK_BELOW_WIDTH
            else Qt.Orientation.Horizontal
        )
        if self._splitter.orientation() != want:
            self._splitter.setOrientation(want)
            if want == Qt.Orientation.Vertical:
                self._splitter.setSizes([self.height() * 6 // 10, self.height() * 4 // 10])
            else:
                self._splitter.setSizes([self.width() * 55 // 100, self.width() * 45 // 100])

    def _apply_always_on_top(self) -> None:
        was_visible = self.isVisible()
        self.setWindowFlag(
            Qt.WindowType.WindowStaysOnTopHint, self.cfg.always_on_top
        )
        if was_visible:
            self.show()  # setWindowFlag masque la fenêtre native

    def _set_pinned(self, pinned: bool) -> None:
        self.cfg.always_on_top = pinned
        self.cfg.save()
        self._apply_always_on_top()

    def _restore_window(self) -> None:
        if self.cfg.splitter_state_b64:
            try:
                self._splitter.restoreState(
                    QByteArray.fromBase64(self.cfg.splitter_state_b64.encode())
                )
            except Exception:  # noqa: BLE001
                pass

        if self.cfg.snap_screenpad_on_launch:
            snap_to_screenpad(self, self.cfg.screenpad_side)
            return

        restored = False
        if self.cfg.window_geometry_b64:
            try:
                restored = self.restoreGeometry(
                    QByteArray.fromBase64(self.cfg.window_geometry_b64.encode())
                )
            except Exception:  # noqa: BLE001
                restored = False
        if restored and self._on_a_screen():
            self.show()
        else:
            snap_to_screenpad(self, self.cfg.screenpad_side)

    def _on_a_screen(self) -> bool:
        centre = self.frameGeometry().center()
        return any(s.availableGeometry().contains(centre) for s in QGuiApplication.screens())

    def _save_window(self) -> None:
        self.cfg.window_geometry_b64 = bytes(self.saveGeometry().toBase64()).decode()
        self.cfg.splitter_state_b64 = bytes(self._splitter.saveState().toBase64()).decode()
        self.cfg.save()

    # ---------------------------------------------------------------- tray
    def _build_tray(self) -> None:
        icon_path = ASSETS_DIR / "amphi.ico"
        icon = (
            QIcon(str(icon_path))
            if icon_path.exists()
            else self.style().standardIcon(self.style().StandardPixmap.SP_MediaVolume)
        )
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("Amphi")
        menu = QMenu()
        menu.addAction(QAction("Afficher", self, triggered=self._reveal))
        menu.addAction(QAction("Réglages…", self, triggered=self._open_settings))
        menu.addSeparator()
        menu.addAction(QAction("Quitter", self, triggered=self.close))
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._reveal()

    def _reveal(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # -------------------------------------------------------- enregistrement
    def _on_recording_started(self) -> None:
        self._update_sleep_lock(True)
        self._glitch_seen = False
        if self.cfg.transcribe_while_recording:
            self.ensure_worker()  # on laisse la file avancer pendant la capture

    def _on_silence_alert(self, silent: bool) -> None:
        if silent:
            self.setWindowTitle("Amphi  ⚠  micro muet ?")
            self._tray.setToolTip("Amphi — ⚠ aucun son détecté")
            self._tray.showMessage(
                "Amphi — aucun son détecté",
                "Le micro ne capte rien depuis 12 s. Vérifie qu'il n'est pas coupé "
                "(touche micro du clavier / paramètres son Windows).",
                QSystemTrayIcon.MessageIcon.Warning,
                8000,
            )
            QApplication.alert(self)  # fait clignoter le bouton dans la barre des tâches
        else:
            self.setWindowTitle("Amphi  ●  enregistrement")
            self._tray.setToolTip("Amphi — ● enregistrement")

    def _on_glitch_alert(self, total: int) -> None:
        QApplication.alert(self)
        if not self._glitch_seen:
            self._glitch_seen = True
            self._tray.showMessage(
                "Amphi — coupures audio",
                "Le CPU sature : des bribes de l'enregistrement en cours sont perdues. "
                "Décoche « Continuer la transcription pendant l'enregistrement » "
                "dans Réglages > Comportement.",
                QSystemTrayIcon.MessageIcon.Warning,
                10000,
            )

    def _on_recorder_state(self, state: str) -> None:
        recording = state in (RecorderState.RECORDING, RecorderState.PAUSED)
        self._update_sleep_lock(recording)
        self._set_recording_flag(recording)
        if state == RecorderState.RECORDING:
            self.setWindowTitle("Amphi  ●  enregistrement")
        elif state == RecorderState.PAUSED:
            self.setWindowTitle("Amphi  ⏸  en pause")
        else:
            self.setWindowTitle("Amphi")
        self._tray.setToolTip(self.windowTitle())

    def _set_recording_flag(self, recording: bool) -> None:
        """Signale au worker de s'effacer pendant la capture (protège l'audio)."""
        try:
            if recording:
                RECORDING_FLAG.write_text("1", encoding="utf-8")
            else:
                RECORDING_FLAG.unlink(missing_ok=True)
        except OSError:
            log.debug("drapeau .recording", exc_info=True)

    def _update_sleep_lock(self, active: bool) -> None:
        want = active and self.cfg.prevent_sleep
        if want and not self._sleep_held:
            power.inhibit_sleep(keep_display_on=True)
            self._sleep_held = True
        elif not want and self._sleep_held:
            power.release_sleep()
            self._sleep_held = False

    def _on_recording_finished(self, result: RecordingResult) -> None:
        dlg = ValidateDialog(self.cfg, result, self)
        code = dlg.exec()
        if code == ValidateDialog.DELETE:
            shutil.rmtree(result.staging_dir, ignore_errors=True)
            return
        if code != ValidateDialog.DialogCode.Accepted or dlg.result_meta is None:
            log.info("Enregistrement %s laissé en staging (plus tard).", result.staging_dir.name)
            return
        self._queue_recording(dlg.result_meta, result.staging_dir)

    def _queue_recording(self, meta: RecordingMeta, staging_dir: Path) -> None:
        from ..paths import unique_dir

        target = self.cfg.target_dir_for(
            meta.matiere, meta.date, meta.titre, niveau=meta.niveau
        )
        target = unique_dir(target.parent, target.name)
        engine = {
            "model": self.cfg.engine.model,
            "compute_type": self.cfg.engine.compute_type,
            "cpu_threads": self.cfg.engine.cpu_threads,
        }
        rec_id = self.store.add(
            meta, staging_dir=staging_dir, target_dir=target, engine=engine
        )
        log.info("Enregistrement %d mis en file → %s", rec_id, target)
        self.queue_pane.refresh()
        self.ensure_worker()

    # ------------------------------------------------------------- worker
    def is_worker_running(self) -> bool:
        try:
            pid = int(WORKER_LOCK.read_text().strip())
            proc = psutil.Process(pid)
            return "python" in proc.name().lower()
        except (OSError, ValueError, psutil.Error):
            return False

    def ensure_worker(self) -> None:
        if not self.store.pending_exists():
            return
        if RECORDING_FLAG.exists() and not self.cfg.transcribe_while_recording:
            return  # priorité à la capture audio
        if (
            self.cfg.ac_only
            and not power.on_ac_power()
            and not FORCE_BATTERY_FLAG.exists()
        ):
            return  # inutile : il se mettrait en pause immédiatement
        if time.monotonic() - self._last_spawn < 15:
            return
        if self.is_worker_running():
            return
        WORKER_STOP_FLAG.unlink(missing_ok=True)
        try:
            subprocess.Popen(
                [sys.executable, "-m", "amphi.transcribe.worker"],
                cwd=str(APP_DIR),
                creationflags=_DETACHED,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            self._last_spawn = time.monotonic()
            log.info("Worker de transcription lancé.")
        except OSError as exc:
            log.error("Lancement du worker impossible : %s", exc)

    def _tick(self) -> None:
        pending = self.store.pending_exists()
        if pending and (power.on_ac_power() or FORCE_BATTERY_FLAG.exists()):
            self.ensure_worker()
        if not pending and FORCE_BATTERY_FLAG.exists():
            FORCE_BATTERY_FLAG.unlink(missing_ok=True)  # file vide -> retour au mode normal

    def set_force_battery(self, enabled: bool) -> None:
        try:
            if enabled:
                FORCE_BATTERY_FLAG.write_text("1", encoding="utf-8")
                log.info("Transcription forcée sur batterie activée.")
                self.ensure_worker()
            else:
                FORCE_BATTERY_FLAG.unlink(missing_ok=True)
                log.info("Transcription forcée sur batterie désactivée.")
        except OSError:
            log.debug("drapeau .force_battery", exc_info=True)

    # ------------------------------------------------------------ actions file
    def _open_folder(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            QMessageBox.information(self, "Dossier", "Ce dossier n'existe pas (encore).")
            return
        try:
            os.startfile(str(p))  # noqa: S606  (Windows)
        except OSError as exc:
            QMessageBox.warning(self, "Dossier", str(exc))

    def _retry(self, rec_id: int) -> None:
        rec = self.store.get(rec_id)
        if not rec:
            return
        if rec.staging_dir and (Path(rec.staging_dir) / "segments.jsonl").exists():
            (Path(rec.staging_dir) / "segments.jsonl").unlink(missing_ok=True)
        self.store.update(rec_id, status=Status.QUEUED, error="", progress=0.0, stage="")
        self.queue_pane.refresh()
        self.ensure_worker()

    def _retry_summary(self, rec_id: int) -> None:
        self.store.update(rec_id, status=Status.TRANSCRIBED, error="", stage="")
        self.queue_pane.refresh()
        self.ensure_worker()

    def _remove(self, rec_id: int) -> None:
        rec = self.store.get(rec_id)
        if rec and rec.staging_dir:
            shutil.rmtree(rec.staging_dir, ignore_errors=True)
        self.store.delete(rec_id)
        self.queue_pane.refresh()

    # ----------------------------------------------------------- réglages
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        dlg.applied.connect(self._on_settings_applied)
        dlg.exec()

    def _on_settings_applied(self) -> None:
        self.recorder_pane._refresh_mic_label()
        self.recorder_pane.recorder.set_gain_db(self.cfg.mic_gain_db)  # live, même en cours
        self._act_ontop.setChecked(self.cfg.always_on_top)
        self._apply_always_on_top()
        self._refresh_banner()
        self.ensure_worker()

    # -------------------------------------------------- récupération crash
    def _recover_orphans(self) -> None:
        if not STAGING_DIR.exists():
            return
        rows = self.store.all(limit=500)

        for r in rows:  # staging des jobs terminés dont le nettoyage avait échoué
            if r.status == Status.DONE and r.staging_dir and Path(r.staging_dir).exists():
                shutil.rmtree(r.staging_dir, ignore_errors=True)
                if not Path(r.staging_dir).exists():
                    self.store.update(r.id, staging_dir="")

        known = {str(Path(r.staging_dir).resolve()) for r in rows if r.staging_dir}
        # les enregistrements suspendus d'abord (on peut en reprendre un)
        orphans = sorted(
            (d for d in STAGING_DIR.glob("rec_*") if d.is_dir() and str(d.resolve()) not in known),
            key=lambda d: not (d / SIDECAR).exists()
            or "suspended" not in (d / SIDECAR).read_text(encoding="utf-8", errors="ignore"),
        )
        for d in orphans:
            if not (d / CAPTURE_WAV).exists():
                shutil.rmtree(d, ignore_errors=True)
                continue
            if self._offer_recovery(d):  # a repris un enregistrement -> on s'arrête là
                break

    def _offer_recovery(self, d: Path) -> bool:
        """Renvoie True si l'utilisateur a repris cet enregistrement (capture active)."""
        import json

        meta_side = {}
        if (d / SIDECAR).exists():
            try:
                meta_side = json.loads((d / SIDECAR).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta_side = {}
        dur = float(meta_side.get("captured_seconds", 0.0))
        markers = [Marker(**m) for m in meta_side.get("markers", [])]
        sr = int(meta_side.get("sample_rate", self.cfg.sample_rate))
        suspended = bool(meta_side.get("suspended"))

        box = QMessageBox(self)
        if suspended:
            box.setWindowTitle("Reprendre l'enregistrement")
            box.setText(
                "Un enregistrement mis en pause a été retrouvé :\n\n"
                f"• {d.name}\n• {RecordingMeta.hhmmss(dur)} déjà enregistrés"
                f"\n• {len(markers)} marqueur(s)"
            )
            resume = box.addButton("Reprendre", QMessageBox.ButtonRole.AcceptRole)
            classify = box.addButton("Terminer et classer", QMessageBox.ButtonRole.ActionRole)
        else:
            box.setWindowTitle("Enregistrement récupéré")
            box.setText(
                "Un enregistrement non classé a été retrouvé :\n\n"
                f"• {d.name}\n• durée ~ {RecordingMeta.hhmmss(dur)}\n• {len(markers)} marqueur(s)"
            )
            resume = None
            classify = box.addButton("Classer maintenant", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Garder pour plus tard", QMessageBox.ButtonRole.RejectRole)
        drop = box.addButton("Supprimer", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()

        if clicked is drop:
            shutil.rmtree(d, ignore_errors=True)
        elif clicked is resume:
            if self.recorder_pane.is_recording():
                QMessageBox.information(
                    self, "Reprendre", "Termine d'abord l'enregistrement en cours."
                )
                return False
            return self.recorder_pane.resume_recording(d)
        elif clicked is classify:
            from ..audio import encode

            wav = d / CAPTURE_WAV
            if encode.probe_duration(wav) is None:
                fixed = d / "capture_fixed.wav"
                if encode.repair_wav(wav, fixed):
                    fixed.replace(wav)
            self._on_recording_finished(
                RecordingResult(
                    staging_dir=d,
                    wav_path=wav,
                    duration_s=dur or (encode.probe_duration(wav) or 0.0),
                    sample_rate=sr,
                    markers=markers,
                )
            )
        return False

    # -------------------------------------------------------------- quitter
    def closeEvent(self, event) -> None:
        if self._quitting:
            event.accept()
            return
        if self.recorder_pane.is_recording():
            box = QMessageBox(self)
            box.setWindowTitle("Quitter Amphi")
            box.setText(
                "Un enregistrement est en cours.\n\n"
                "« Reprendre plus tard » le met en pause : tu pourras le continuer au "
                "prochain lancement d'Amphi."
            )
            later = box.addButton("Reprendre plus tard", QMessageBox.ButtonRole.AcceptRole)
            stop_it = box.addButton("Arrêter et classer", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is later:
                try:
                    self.recorder_pane.suspend()
                except Exception:  # noqa: BLE001
                    log.exception("Suspension à la fermeture")
            elif clicked is stop_it:
                try:
                    self.recorder_pane.recorder.stop()  # reste en staging -> récup au prochain lancement
                except Exception:  # noqa: BLE001
                    log.exception("Arrêt à la fermeture")
            else:
                event.ignore()
                return

        self._quitting = True
        self._update_sleep_lock(False)
        self._save_window()
        if self.store.pending_exists() and self.is_worker_running():
            self._tray.showMessage(
                "Amphi",
                "Les transcriptions continuent en arrière-plan.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
        self._tray.hide()
        event.accept()
        QApplication.quit()
