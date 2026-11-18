# Crée la tâche planifiée « autoclip » qui remplace tmux sous Windows : au démarrage du PC, même
# sans session ouverte, elle lance le superviseur scripts\run_autoclip.ps1, qui lance
# `autoclip live` et le relance avec un délai croissant s'il s'arrête.
# Usage (PowerShell en administrateur) : powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
#   Voir l'état : Get-ScheduledTask autoclip | Get-ScheduledTaskInfo  |  journal : autoclip.log
#   Arrêter     : Disable-ScheduledTask autoclip; Stop-ScheduledTask autoclip
#                 (arrêt sec : un tri quotidien en cours n'est pas terminé ; il est refait au
#                 prochain lancement s'il n'a pas eu le temps de finir)
#   Relancer    : Enable-ScheduledTask autoclip; Start-ScheduledTask autoclip
#   Supprimer   : Unregister-ScheduledTask autoclip
$root = Split-Path $PSScriptRoot
$exe = Join-Path $root ".venv\Scripts\autoclip.exe"
if (-not (Test-Path $exe)) { Write-Host "autoclip pas installé : lance scripts\setup_windows.ps1"; exit 1 }

# Le superviseur relance autoclip avec un délai croissant (l'option « redémarrer en cas d'échec »
# du Planificateur ne réagit pas à un programme qui s'arrête avec une erreur).
$sup = Join-Path $root "scripts\run_autoclip.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $root `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$sup`""
# Au démarrage du PC, puis toutes les 5 min par sécurité : si le superviseur tourne déjà, la
# nouvelle instance est ignorée ; s'il a été tué, il repart.
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$triggers = @((New-ScheduledTaskTrigger -AtStartup), $repeat)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit 0 `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
# S4U : tourne sans session ouverte, sans enregistrer de mot de passe.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName autoclip -Action $action -Trigger $triggers -Settings $settings `
    -Principal $principal -Force -ErrorAction Stop | Out-Null
Write-Host "Tâche « autoclip » créée : autoclip live démarre dans 5 min au plus, et à chaque démarrage du PC."
Write-Host "Tout de suite : Start-ScheduledTask autoclip   |   journal : autoclip.log"
