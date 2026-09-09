"""Journalisation. `setup()` est appelé UNE fois par processus (app / worker) ;
les modules font simplement `log = logging.getLogger(__name__)`.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .paths import LOG_DIR

_CONFIGURED = False


def setup(process_name: str, *, console: bool = False) -> logging.Logger:
    """Configure le logger racine pour ce processus. Idempotent."""
    global _CONFIGURED
    root = logging.getLogger()
    if not _CONFIGURED:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s")

        fh = RotatingFileHandler(
            LOG_DIR / f"{process_name}.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

        if console and sys.stderr is not None:
            ch = logging.StreamHandler()
            ch.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
            root.addHandler(ch)

        root.setLevel(logging.INFO)
        logging.getLogger("faster_whisper").setLevel(logging.WARNING)
        _CONFIGURED = True
    return logging.getLogger(process_name)
