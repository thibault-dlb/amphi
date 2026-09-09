"""Exécute un appel bloquant (réseau, disque) hors du thread UI."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


class CallableTask(QThread):
    done = Signal(object)      # résultat
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


def run_async(fn: Callable[[], Any], on_done, on_error=None, parent=None) -> CallableTask:
    task = CallableTask(fn, parent)
    task.done.connect(on_done)
    if on_error:
        task.failed.connect(on_error)
    task.finished.connect(task.deleteLater)
    task.start()
    return task
