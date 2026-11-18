# Superviseur : lance `autoclip live` et le relance s'il s'arrête, avec un délai croissant
# (30 s, 1 min, 2 min, 4 min… jusqu'à 15 min ; le délai repart à 30 s si autoclip a tourné
# plus d'une heure). Lancé au démarrage de Windows par la tâche planifiée (install_task.ps1).
# Journal du superviseur : autoclip-superviseur.log ; plantages Python et messages de Streamlink :
# un fichier autoclip-erreurs-<date>.log par lancement (les 5 derniers sont gardés). Un seul
# fichier commun pouvait rester verrouillé par un process survivant et bloquer toutes les relances.
Set-Location (Split-Path $PSScriptRoot)
$exe = Join-Path (Get-Location) ".venv\Scripts\autoclip.exe"
$log = "autoclip-superviseur.log"
$delay = 30
while ($true) {
    $start = Get-Date
    $err = "autoclip-erreurs-$(Get-Date -Format yyyyMMdd-HHmmss).log"
    Get-ChildItem autoclip-erreurs-*.log | Sort-Object LastWriteTime -Descending | Select-Object -Skip 4 |
        Remove-Item -ErrorAction SilentlyContinue
    Add-Content -Encoding UTF8 $log "$(Get-Date -Format s) lancement de autoclip live (erreurs : $err)"
    cmd /c "`"$exe`" live > nul 2> $err"
    $code = $LASTEXITCODE
    $ran = [int]((Get-Date) - $start).TotalSeconds
    if ($ran -gt 3600) { $delay = 30 }
    Add-Content -Encoding UTF8 $log "$(Get-Date -Format s) autoclip s'est arrêté (code $code) après $ran s ; relance dans $delay s"
    Start-Sleep -Seconds $delay
    $delay = [Math]::Min($delay * 2, 900)
}
