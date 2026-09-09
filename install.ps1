<#
  Installe Amphi : venv, dependances, modele Whisper, icone, benchmark, raccourcis.

    powershell -ExecutionPolicy Bypass -File install.ps1
    ... -Quick          benchmark rapide (~4 min au lieu de ~13 min)
    ... -SkipBenchmark  ne pas lancer le benchmark (a faire depuis les Reglages)
    ... -SkipModel      ne pas pre-telecharger le modele (~3 Go)

  (Script en ASCII pur : Windows PowerShell 5.1 lit mal l'UTF-8 sans BOM.)
#>
[CmdletBinding()]
param(
    [switch]$Quick,
    [switch]$SkipBenchmark,
    [switch]$SkipModel
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$VenvPyw = Join-Path $Venv "Scripts\pythonw.exe"

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# --- 1. Python 3.11+ ---------------------------------------------------------
Step "Python"
$pyExe = $null; $pyArgs = @()
try {
    $v = & py -3.11 --version 2>&1
    if ($v -match "3\.1[1-9]|3\.[2-9][0-9]") { $pyExe = "py"; $pyArgs = @("-3.11") }
} catch {}
if (-not $pyExe) {
    try {
        $v = & python --version 2>&1
        if ($v -match "3\.1[1-9]|3\.[2-9][0-9]") { $pyExe = "python" }
    } catch {}
}
if (-not $pyExe) { throw "Python 3.11+ introuvable (essaye 'py -3.11' et 'python')." }
Write-Host "  $pyExe $pyArgs -> $v"

# --- 2. venv ---------------------------------------------------------------
Step "Environnement virtuel"
if (-not (Test-Path $VenvPy)) {
    & $pyExe @pyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "creation du venv echouee." }
    Write-Host "  venv cree : $Venv"
} else {
    Write-Host "  venv deja present"
}

# --- 3. dependances -------------------------------------------------------
Step "Dependances (quelques minutes)"
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install a echoue." }

# --- 4. ffmpeg ---------------------------------------------------------
Step "ffmpeg"
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host ("  ok : " + (Get-Command ffmpeg).Source)
} else {
    Write-Warning "  ffmpeg absent du PATH. Installe-le : winget install Gyan.FFmpeg"
}

# --- 5. icone --------------------------------------------------------
Step "Icone"
& $VenvPy (Join-Path $Root "tools\make_icon.py")

# --- 6. modele -------------------------------------------------
if (-not $SkipModel) {
    Step "Telechargement du modele large-v3-turbo (~1.6 Go, une seule fois)"
    & $VenvPy -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cpu', compute_type='int8'); print('  modele pret')"
    if ($LASTEXITCODE -ne 0) { Write-Warning "  telechargement incomplet - il se fera au 1er usage." }
}

# --- 7. benchmark -----------------------------------------------
if (-not $SkipBenchmark) {
    Step "Benchmark sur cette machine"
    $benchArgs = @((Join-Path $Root "tools\benchmark.py"))
    if ($Quick) { $benchArgs += "--quick" }
    & $VenvPy @benchArgs
    if ($LASTEXITCODE -ne 0) { Write-Warning "  benchmark non abouti - relance depuis Reglages > Micro et moteur." }
}

# --- 8. raccourcis -------------------------------------------
Step "Raccourcis"
$icon = Join-Path $Root "amphi\assets\amphi.ico"
$desktop = [Environment]::GetFolderPath("Desktop")
$programs = [Environment]::GetFolderPath("Programs")
$targets = @((Join-Path $desktop "Amphi.lnk"), (Join-Path $programs "Amphi.lnk"))
$wsh = New-Object -ComObject WScript.Shell
foreach ($path in $targets) {
    $lnk = $wsh.CreateShortcut($path)
    $lnk.TargetPath = $VenvPyw
    $lnk.Arguments = "-m amphi"
    $lnk.WorkingDirectory = $Root
    if (Test-Path $icon) { $lnk.IconLocation = $icon }
    $lnk.Description = "Enregistrer et transcrire un cours"
    $lnk.WindowStyle = 7
    $lnk.Save()
    Write-Host "  $path"
}

Step "Termine"
Write-Host "Lance Amphi depuis le raccourci du Bureau." -ForegroundColor Green
Write-Host "Au 1er lancement : renseigne ton niveau, tes matieres, et ta cle Gemini." -ForegroundColor Green
