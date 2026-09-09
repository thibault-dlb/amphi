"""Découverte des niveaux et matières à partir de l'arborescence OneDrive\\Cours.

L'utilisateur a déjà `Cours\\B1\\<matière>`, `Cours\\B2\\<matière>`, etc. On lit ces
dossiers pour pré-remplir la liste ; l'ajout/retrait manuel prend ensuite le dessus.
"""

from __future__ import annotations

from pathlib import Path

_IGNORED = {
    "enregistrements",
    "recordings",
    "divers",  # dossier de repli créé par Amphi quand niveau/matière manque — pas un vrai niveau
    "$recycle.bin",
    "system volume information",
}


def _visible_dirs(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    out = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith((".", "~", "$")) or name.casefold() in _IGNORED:
            continue
        out.append(name)
    return sorted(out, key=str.casefold)


def discover_levels(courses_root: Path) -> list[str]:
    """Sous-dossiers de premier niveau (B1, B2, Terminale…)."""
    return _visible_dirs(courses_root)


def discover_subjects(courses_root: Path, niveau: str) -> list[str]:
    """Matières sous <courses_root>/<niveau>."""
    if not niveau:
        return []
    return _visible_dirs(courses_root / niveau)


def merge_subjects(existing: list[str], discovered: list[str]) -> list[str]:
    """Union ordonnée : garde l'ordre choisi par l'utilisateur, ajoute les nouvelles."""
    seen = {s.casefold() for s in existing}
    merged = list(existing)
    for s in discovered:
        if s.casefold() not in seen:
            merged.append(s)
            seen.add(s.casefold())
    return merged
