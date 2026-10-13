"""Lancement de Claude Code sur le skill /maj-moodle dans une console visible.

Deux modes :
- automatique (`claude -p`) : aucune question, permissions limitées au script du skill et
  à la lecture ; la console se ferme dès que Claude a fini (pas de compte rendu) ;
- interactif : session Claude Code classique, fermée par l'utilisateur.
Le script PowerShell est écrit dans un fichier temporaire (pas de guillemets imbriqués).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..paths import APP_DIR

MODELE = "sonnet"       # alias Claude Code : toujours le Sonnet le plus récent

CONSIGNE_AUTO = (
    "/maj-moodle mode automatique (voir la section « Mode automatique » du skill). "
    "Commandes du script, à utiliser telles quelles : {py} {driver} plan | apply | update."
)


def _ps(texte: str) -> str:
    """Chaîne PowerShell entre apostrophes (apostrophes doublées)."""
    return "'" + texte.replace("'", "''") + "'"


def chemins() -> tuple[str | None, Path, Path]:
    """(chemin de claude ou None, python du venv d'Amphi, driver du skill)."""
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe":
        py = py.with_name("python.exe")
    driver = APP_DIR / ".claude" / "skills" / "maj-moodle" / "driver.py"
    return shutil.which("claude"), py, driver


def outils_autorises(py: Path, driver: Path) -> list[str]:
    """Permissions accordées d'avance en mode automatique (tout le reste est refusé)."""
    py_s = py.as_posix()
    return [
        "Skill", "Read", "Glob", "Grep",
        f"Bash({py_s}:*)", f"Bash({py}:*)", f"PowerShell({py}:*)", f"PowerShell(& {py}:*)",
    ]


def script_powershell(claude: str, interactif: bool, py: Path, driver: Path,
                      consigne: str | None = None) -> str:
    """Contenu du script .ps1 lancé dans la console."""
    lignes = [
        "$Host.UI.RawUI.WindowTitle = 'Amphi — Claude /maj-moodle'",
        f"Set-Location -LiteralPath {_ps(str(APP_DIR))}",
    ]
    if interactif:
        lignes.append(f"& {_ps(claude)} --model {MODELE} '/maj-moodle'")
        return "\n".join(lignes) + "\n"
    texte = (consigne or CONSIGNE_AUTO).format(py=f'"{py}"', driver=f'"{driver}"')
    outils = ",".join(outils_autorises(py, driver))
    lignes += [
        "Write-Host 'Claude Code (Sonnet) met à jour les documents Moodle en mode automatique…'"
        " -ForegroundColor Cyan",
        "Write-Host 'La fenêtre se fermera toute seule ; Amphi mettra ensuite son tableau à jour.'"
        " -ForegroundColor DarkGray",
        "Write-Host ''",
        f"& {_ps(claude)} -p {_ps(texte)} --model {MODELE} --allowedTools {_ps(outils)} | Out-Null",
        "exit $LASTEXITCODE",
    ]
    return "\n".join(lignes) + "\n"


def lancer(interactif: bool, consigne: str | None = None) -> subprocess.Popen:
    """Ouvre la console. Lève FileNotFoundError si `claude` est introuvable."""
    claude, py, driver = chemins()
    if not claude:
        raise FileNotFoundError("claude")
    contenu = script_powershell(claude, interactif, py, driver, consigne)
    fichier = Path(tempfile.gettempdir()) / f"amphi_maj_moodle_{'inter' if interactif else 'auto'}.ps1"
    fichier.write_text(contenu, encoding="utf-8-sig")   # BOM : accents lus correctement par PowerShell 5
    return subprocess.Popen(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(fichier)],
        cwd=str(APP_DIR),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
