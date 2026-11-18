# Installe twitch-autoclip sur ce PC Windows. Relançable sans risque (idempotent).
# Usage : powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
# Installe ffmpeg avec winget s'il manque ; le reste va dans le venv (.venv), sans droits admin.
Set-Location (Split-Path $PSScriptRoot)
$missing = @()
function Ok($msg) { Write-Host "  [OK] $msg" }
function Miss($msg, $fix) { Write-Host "  [!!] $msg"; Write-Host "       -> $fix"; $script:missing += $msg }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Host "== Outils système"
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py -and (& python -c "import sys; print(sys.version_info >= (3, 11))") -eq "True") {
    Ok "$(& python --version) ($($py.Source))"
} else {
    Miss "Python 3.11 ou plus" "winget install Python.Python.3.12, puis rouvre le terminal"
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "  [..] ffmpeg absent : winget install Gyan.FFmpeg"
    winget install --id Gyan.FFmpeg -e --source winget --accept-package-agreements --disable-interactivity
    Refresh-Path
}
if ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Ok (((& ffmpeg -version | Select-Object -First 1) -split " " | Select-Object -First 3) -join " ")
    if (& ffmpeg -hide_banner -filters 2>$null | Select-String " ass ") {
        Ok "ffmpeg sait incruster les sous-titres (libass)"
    } else {
        Miss "ffmpeg avec libass" "le build Gyan.FFmpeg l'a : winget install Gyan.FFmpeg"
    }
} else {
    Miss "ffmpeg / ffprobe" "winget install Gyan.FFmpeg, puis rouvre le terminal"
}
if (Test-Path "$env:SystemRoot\System32\msvcp140.dll") {
    Ok "runtime Visual C++ 2015+ (nécessaire à Whisper)"
} else {
    Write-Host "  [..] runtime Visual C++ absent : winget install Microsoft.VCRedist.2015+.x64 (accepter l'invite admin)"
    winget install --id Microsoft.VCRedist.2015+.x64 -e --source winget --accept-package-agreements --disable-interactivity
    if (-not (Test-Path "$env:SystemRoot\System32\msvcp140.dll")) {
        Miss "runtime Visual C++ 2015+" "winget install Microsoft.VCRedist.2015+.x64 et accepter l'invite admin"
    }
}
$free = [math]::Round((Get-PSDrive (Get-Location).Drive.Name).Free / 1GB, 1)
Write-Host "  [..] espace libre ici : $free Go (compter ~3 Go pour le buffer du mode live)"

Write-Host "== Environnement Python (.venv)"
if (-not (Test-Path .venv\Scripts\python.exe) -and $py) {
    & python -m venv .venv
    if (-not $?) { Miss "création du venv" "regarde l'erreur ci-dessus" }
}
if (Test-Path .venv\Scripts\python.exe) {
    & .venv\Scripts\python.exe -m pip install -q -e ".[whisper]"
    if ($?) { Ok "dépendances installées (pip install -e "".[whisper]"")" }
    else { Miss "dépendances Python" "regarde l'erreur pip ci-dessus" }
}

Write-Host "== Configuration"
if (Test-Path config.toml) {
    Ok "config.toml existe déjà (non modifié)"
} else {
    Copy-Item config.example.toml config.toml
    Ok "config.toml créé depuis config.example.toml"
}

if (Test-Path .venv\Scripts\autoclip.exe) {
    Write-Host "== Modèle Whisper"
    $model = & .venv\Scripts\python.exe -c "from autoclip.config import load_config; print(load_config().subtitles.model)"
    & .venv\Scripts\python.exe -c "import sys; from faster_whisper import download_model; download_model(sys.argv[1])" $model
    if ($?) { Ok "modèle Whisper '$model' téléchargé (cache %USERPROFILE%\.cache\huggingface)" }
    else { Miss "modèle Whisper '$model'" "vérifie l'accès réseau à huggingface.co puis relance ce script" }

    Write-Host "== autoclip check --live"
    & .venv\Scripts\autoclip.exe check --live
    if (-not $?) { Miss "autoclip check" "voir les lignes [!!] juste au-dessus" }
}

Write-Host ""
if ($missing.Count -eq 0) {
    Write-Host "Tout est prêt. Relance automatique : scripts\install_task.ps1 (PowerShell en administrateur)"
} else {
    Write-Host "Il manque $($missing.Count) chose(s) :"
    $missing | ForEach-Object { Write-Host "  - $_" }
    exit 1
}
