"""Ligne de commande de la synchro Moodle.

    python -m amphi.moodle sync [--dry-run] [--no-icloud]

--no-icloud : lit Moodle et simule la synchro contre un calendrier vide en
mémoire (aucun trousseau ni identifiant iCloud requis).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from ..config import Config
from ..logging_setup import setup
from .client import MoodleError
from .sync import PARIS, NullCalendar, load_devoirs, run_sync, sync


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m amphi.moodle")
    sub = parser.add_subparsers(dest="commande", required=True)
    p_sync = sub.add_parser("sync", help="Copier les devoirs Moodle vers l'agenda iCloud")
    p_sync.add_argument("--dry-run", action="store_true", help="Simuler sans rien écrire")
    p_sync.add_argument(
        "--no-icloud",
        action="store_true",
        help="Lire Moodle et simuler contre un calendrier vide (sans iCloud)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    setup("moodle", console=True)
    cfg = Config.load()

    if args.no_icloud:
        try:
            devoirs = load_devoirs(cfg)
        except MoodleError as e:
            print(f"Erreur : {e}", file=sys.stderr)
            return 1
        rapport = sync(devoirs, NullCalendar(), datetime.now(PARIS), dry_run=True)
    else:
        rapport = run_sync(cfg, dry_run=args.dry_run)

    print(rapport.to_text())
    return 1 if rapport.erreurs else 0


if __name__ == "__main__":
    raise SystemExit(main())
