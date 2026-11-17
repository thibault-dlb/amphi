"""Import des enregistrements déjà livrés dans OneDrive vers l'historique local (`amphi.db`).

La file d'attente / l'historique d'Amphi lit la base SQLite locale, qui n'est PAS synchronisée
par OneDrive : sur un nouveau PC, ou après une réinstallation, elle est vide alors que
`<racine>\\<niveau>\\<matière>\\Enregistrements\\<date>_<titre>\\` contient tous les cours.

`import_existing` recrée une ligne par dossier de cours, à partir de son `meta.json` :

- **lecture seule sur OneDrive** : aucun fichier n'est écrit, déplacé ni supprimé ;
- **idempotent** : un dossier déjà connu (même `target_dir`) est ignoré ;
- **sans effet de bord** : seuls les statuts « terminé » et « résumé échoué » sont posés, aucun
  n'est repris par le worker (donc rien n'est retranscrit ni envoyé à Gemini sans clic) ;
- `staging_dir` reste VIDE : l'interface supprime `staging_dir` quand on retire une ligne, il ne
  doit jamais pointer vers un dossier de cours.

En ligne de commande :

    python -m amphi.library.importer              # niveau des Réglages
    python -m amphi.library.importer --dry-run    # affiche ce qui serait importé
    python -m amphi.library.importer --all-levels # B1, B2, B3…
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..models import RecordingMeta, Status
from . import scan
from .store import Store

log = logging.getLogger(__name__)

_AUDIO_NAMES = ("audio.flac", "audio.wav", "audio.opus", "audio.ogg", "audio.m4a")


@dataclass
class ImportReport:
    imported: int = 0
    already_known: int = 0
    no_meta: list[str] = field(default_factory=list)          # dossier sans meta.json lisible
    no_transcript: list[str] = field(default_factory=list)    # meta.json mais pas de transcript.md
    by_matiere: dict[str, int] = field(default_factory=dict)  # nouveaux cours par matière

    def summary(self) -> str:
        parts = [f"{self.imported} importé(s)", f"{self.already_known} déjà connu(s)"]
        if self.no_meta:
            parts.append(f"{len(self.no_meta)} sans meta.json")
        if self.no_transcript:
            parts.append(f"{len(self.no_transcript)} sans transcript")
        return " · ".join(parts)


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _first_existing(folder: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        p = folder / name
        if p.is_file():
            return p
    return None


def _read_meta(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def import_existing(
    store: Store,
    courses_root: Path,
    niveaux: str | list[str],
    recordings_subdir: str = "Enregistrements",
    *,
    dry_run: bool = False,
) -> ImportReport:
    """Ajoute à `store` les cours livrés sous `courses_root/<niveau>/<matière>/<recordings_subdir>`."""
    if isinstance(niveaux, str):
        niveaux = [niveaux]
    report = ImportReport()
    known = {_norm(t) for t in store.target_dirs()}

    for niveau in (n for n in niveaux if n):
        for matiere in scan.discover_subjects(courses_root, niveau):
            rec_root = courses_root / niveau / matiere / recordings_subdir
            if not rec_root.is_dir():
                continue
            for folder in sorted(rec_root.iterdir(), key=lambda p: p.name):
                if not folder.is_dir() or folder.name.startswith("."):
                    continue
                if _norm(folder) in known:
                    report.already_known += 1
                    continue

                raw = _read_meta(folder)
                if raw is None:
                    report.no_meta.append(str(folder))
                    continue
                transcript = folder / "transcript.md"
                if not transcript.is_file():
                    report.no_transcript.append(str(folder))
                    continue

                resume = folder / "resume.md"
                has_resume = resume.is_file()
                meta = RecordingMeta.from_dict(raw)
                meta.matiere = matiere          # le dossier fait foi (il a pu être renommé)
                meta.niveau = niveau
                meta.titre = meta.titre or folder.name
                meta.date = meta.date or folder.name[:10]
                try:
                    created = float(raw.get("created_at") or transcript.stat().st_mtime)
                except (OSError, TypeError, ValueError):
                    created = 0.0

                report.imported += 1
                report.by_matiere[matiere] = report.by_matiere.get(matiere, 0) + 1
                known.add(_norm(folder))
                if dry_run:
                    continue
                audio = _first_existing(folder, _AUDIO_NAMES)
                store.add_imported(
                    meta,
                    target_dir=folder,
                    status=Status.DONE if has_resume else Status.SUMMARY_FAILED,
                    audio_path=audio,
                    transcript_path=transcript,
                    resume_path=resume if has_resume else None,
                    engine=raw.get("engine") if isinstance(raw.get("engine"), dict) else {},
                    created_at=created,
                    stage="importé" if has_resume else "importé · pas de résumé",
                )
    if report.imported and not dry_run:
        log.info("Import de l'historique : %s", report.summary())
    return report


def main(argv: list[str] | None = None) -> int:
    from ..config import Config

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="n'écrit rien, affiche le résultat")
    ap.add_argument("--all-levels", action="store_true", help="tous les niveaux (B1, B2, B3…)")
    ap.add_argument("--niveau", default=None, help="niveau à importer (défaut : Réglages)")
    ap.add_argument("--root", default=None, help="racine des cours (défaut : Réglages)")
    args = ap.parse_args(argv)

    cfg = Config.load()
    root = Path(args.root) if args.root else cfg.courses_root_path()
    if args.all_levels:
        niveaux = scan.discover_levels(root)
    else:
        niveaux = [args.niveau or cfg.niveau]
    if not any(niveaux):
        print("Aucun niveau : renseigne-le dans Réglages > Rangement, ou passe --niveau.")
        return 2

    store = Store()
    try:
        rep = import_existing(store, root, niveaux, cfg.recordings_subdir, dry_run=args.dry_run)
    finally:
        store.close()

    print(f"{'[simulation] ' if args.dry_run else ''}Racine : {root} · niveaux : {', '.join(niveaux)}")
    for matiere, n in sorted(rep.by_matiere.items(), key=lambda kv: kv[0].casefold()):
        print(f"  {n:3d}  {matiere}")
    print(rep.summary())
    for label, items in (("Sans meta.json (ignorés)", rep.no_meta),
                         ("Sans transcript (ignorés)", rep.no_transcript)):
        if items:
            print(f"{label} :")
            for p in items:
                print(f"  - {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
