"""Détection du ScreenPad Plus + ancrage de la fenêtre sur une de ses moitiés,
via le **vrai snap Windows** (Win+flèche) pour que le redimensionnement soit
solidaire de l'application ancrée sur l'autre moitié.

Le ScreenPad Plus du UX482EA fait 1920×515 : rapport largeur/hauteur ~3.7,
invariant au facteur d'échelle. On le repère par ce ratio plutôt que par des
coordonnées en dur (Windows en remonte trois selon le mode de conscience DPI).
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QRect, QTimer
from PySide6.QtGui import QGuiApplication, QScreen

log = logging.getLogger(__name__)

WIDE_RATIO = 3.0
MIN_WIDTH = 440

# -- clavier synthétique pour déclencher le vrai snap Windows -------------
# SendInput envoie les 4 événements en un seul bloc atomique : Windows voit le
# chord « Win+flèche » et NE déclenche PAS le menu Démarrer au relâchement du Win
# (ce que ferait keybd_event à cause du délai entre appels).
if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _VK_LWIN, _VK_LEFT, _VK_RIGHT = 0x5B, 0x25, 0x27
    _KEYEVENTF_KEYUP, _KEYEVENTF_EXTENDED = 0x0002, 0x0001
    _INPUT_KEYBOARD = 1

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", _KEYBDINPUT)]

        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    def _win_arrow(vk_arrow: int) -> None:
        extra = ctypes.c_ulong(0)
        pextra = ctypes.pointer(extra)

        def ev(vk: int, up: bool, ext: bool = False) -> _INPUT:
            flags = (_KEYEVENTF_KEYUP if up else 0) | (_KEYEVENTF_EXTENDED if ext else 0)
            inp = _INPUT()
            inp.type = _INPUT_KEYBOARD
            inp.ki = _KEYBDINPUT(vk, 0, flags, 0, pextra)
            return inp

        seq = (_INPUT * 4)(
            ev(_VK_LWIN, False),
            ev(vk_arrow, False, True),
            ev(vk_arrow, True, True),
            ev(_VK_LWIN, True),
        )
        _user32.SendInput(4, ctypes.byref(seq), ctypes.sizeof(_INPUT))

    def _force_foreground(hwnd: int) -> bool:
        """Contourne le verrou de premier plan de Windows (AttachThreadInput)."""
        if _user32.GetForegroundWindow() == hwnd:
            return True
        fg = _user32.GetForegroundWindow()
        fg_thread = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
        our_thread = _kernel32.GetCurrentThreadId()
        attached = bool(
            fg_thread and fg_thread != our_thread
            and _user32.AttachThreadInput(our_thread, fg_thread, True)
        )
        try:
            _user32.BringWindowToTop(hwnd)
            _user32.SetForegroundWindow(hwnd)
            _user32.SetActiveWindow(hwnd)
        finally:
            if attached:
                _user32.AttachThreadInput(our_thread, fg_thread, False)
        return _user32.GetForegroundWindow() == hwnd


def describe() -> list[str]:
    out = []
    primary = QGuiApplication.primaryScreen()
    for s in QGuiApplication.screens():
        g = s.geometry()
        ratio = g.width() / g.height() if g.height() else 0
        tag = " [principal]" if s is primary else ""
        out.append(
            f"{s.name()}{tag} — {g.width()}×{g.height()} @ ({g.x()},{g.y()}) "
            f"ratio {ratio:.2f}, échelle {s.devicePixelRatio():.2f}"
        )
    return out


def find_screenpad() -> QScreen | None:
    primary = QGuiApplication.primaryScreen()
    wide = [
        s
        for s in QGuiApplication.screens()
        if s.geometry().height() and s.geometry().width() / s.geometry().height() >= WIDE_RATIO
    ]
    if not wide:
        return None
    non_primary = [s for s in wide if s is not primary]
    return (non_primary or wide)[0]


def target_screen() -> QScreen:
    if sp := find_screenpad():
        return sp
    others = [s for s in QGuiApplication.screens() if s is not QGuiApplication.primaryScreen()]
    return others[0] if others else QGuiApplication.primaryScreen()


def half_rect(screen: QScreen, side: str) -> QRect:
    g = screen.availableGeometry()
    w = max(MIN_WIDTH, g.width() // 2)
    x = g.x() + (g.width() - w) if side == "right" else g.x()
    return QRect(x, g.y(), w, g.height())


def _apply_rect(widget, rect: QRect) -> None:
    if widget.isMaximized() or widget.isFullScreen():
        widget.showNormal()
    widget.setGeometry(rect)
    # multi-DPI : le 1er setGeometry est parfois ignoré tant que le cadre natif
    # n'a pas ses marges -> on force aussi via move/resize
    widget.move(rect.topLeft())
    widget.resize(rect.size())


def _roughly_at(widget, rect: QRect, tol: int = 90) -> bool:
    fg = widget.frameGeometry()
    return (
        abs(fg.x() - rect.x()) <= tol
        and abs(fg.width() - rect.width()) <= max(tol, rect.width() // 4)
    )


def snap_to_screenpad(widget, side: str = "right", screen: QScreen | None = None) -> None:
    """Ancre `widget` sur la moitié `side` ("right"/"left") du ScreenPad.

    Positionne d'abord la fenêtre par géométrie (fiable, sans effet de bord), puis
    tente le **vrai snap Windows** (SendInput Win+flèche, bloc atomique) pour que la
    poignée de partage soit commune avec l'app de l'autre moitié. Un filet remet la
    géométrie si le snap dérape.
    """
    screen = screen or target_screen()
    rect = half_rect(screen, side)
    log.info(
        "Ancrage ScreenPad : écran %s @ %s, moitié %s -> %s",
        screen.name(), screen.geometry(), side, rect,
    )

    # 1) placer la fenêtre AVANT le premier affichage (évite le flash)
    widget.winId()  # réalise la fenêtre native sans l'afficher
    handle = widget.windowHandle()
    if handle is not None and handle.screen() is not screen:
        handle.setScreen(screen)
    _apply_rect(widget, rect)
    widget.show()
    widget.raise_()
    widget.activateWindow()
    QTimer.singleShot(0, lambda: _apply_rect(widget, rect))

    if sys.platform != "win32":
        return

    hwnd = int(widget.winId())

    def _native_snap() -> None:
        _apply_rect(widget, rect)
        try:
            ok = _force_foreground(hwnd)
        except Exception:  # noqa: BLE001
            ok = False
            log.debug("force_foreground", exc_info=True)
        if ok:
            try:
                _win_arrow(_VK_RIGHT if side == "right" else _VK_LEFT)
                QTimer.singleShot(150, widget.activateWindow)  # referme le Snap Assist
            except Exception:  # noqa: BLE001
                log.debug("SendInput", exc_info=True)
        else:
            log.info("Snap natif sauté (impossible de passer au premier plan)")
        QTimer.singleShot(700, _safety_net)

    def _safety_net() -> None:
        if not _roughly_at(widget, rect):
            log.info("Snap dérapé (%s) -> remise en géométrie", widget.frameGeometry())
            _apply_rect(widget, rect)

    QTimer.singleShot(300, _native_snap)


# rétro-compat : ancien nom
def place_left_half(widget, screen: QScreen | None = None) -> None:
    snap_to_screenpad(widget, "left", screen)
