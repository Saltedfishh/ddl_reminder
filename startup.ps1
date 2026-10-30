param([ValidateSet('Install','Remove')][string]$Action = 'Install')
$ErrorActionPreference = 'Stop'
$startupDir = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startupDir 'Canvas DDL Reminder.lnk'
if ($Action -eq 'Remove') {
    if (Test-Path -LiteralPath $linkPath) { Remove-Item -LiteralPath $linkPath }
    Write-Host 'Startup shortcut removed. Running instance is unchanged.'
    exit
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'))) { throw 'Run setup.ps1 first.' }
$shellObj = New-Object -ComObject WScript.Shell
$shortcut = $shellObj.CreateShortcut($linkPath)
$shortcut.TargetPath = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$shortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'background.pyw') + '"'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = 'Canvas local deadline reminders'
$shortcut.Save()
Write-Host 'Installed startup shortcut. It takes effect at next sign-in.'
