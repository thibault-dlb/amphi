"""Lanceur sans console (cible de secours pour un raccourci)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from amphi.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
