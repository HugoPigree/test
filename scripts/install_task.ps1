# Crée la tâche planifiée « autoclip » qui remplace tmux sous Windows : `autoclip live` démarre
# avec le PC, même sans session ouverte, et repart dans les 5 min s'il s'arrête.
# Usage (PowerShell en administrateur) : powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
#   Voir l'état : Get-ScheduledTask autoclip | Get-ScheduledTaskInfo  |  journal : autoclip.log
#   Arrêter     : Disable-ScheduledTask autoclip; Stop-ScheduledTask autoclip
#                 (arrêt sec : les clips en file d'attente ne sont pas terminés, contrairement à Ctrl+C)
#   Relancer    : Enable-ScheduledTask autoclip; Start-ScheduledTask autoclip
#   Supprimer   : Unregister-ScheduledTask autoclip
$root = Split-Path $PSScriptRoot
$exe = Join-Path $root ".venv\Scripts\autoclip.exe"
if (-not (Test-Path $exe)) { Write-Host "autoclip pas installé : lance scripts\setup_windows.ps1"; exit 1 }

# cmd garde les plantages Python (sur stderr) dans autoclip-erreurs.log ; le reste va dans autoclip.log.
$action = New-ScheduledTaskAction -Execute "cmd.exe" -WorkingDirectory $root `
    -Argument "/c `"`"$exe`" live > nul 2>> autoclip-erreurs.log`""
# Au démarrage du PC, puis toutes les 5 min : si autoclip tourne déjà, la nouvelle instance est
# ignorée ; s'il s'est arrêté, il repart. (L'option « redémarrer en cas d'échec » ne réagit pas à
# un programme qui s'arrête avec une erreur.)
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
