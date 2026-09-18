[CmdletBinding()]
param(
    [string]$Repository = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$repositoryPath = (Resolve-Path -LiteralPath $Repository).Path
$source = Join-Path $repositoryPath 'scripts\kai_dev_hub.py'
if (-not (Test-Path -LiteralPath $source)) {
    throw "KAI Developer Hub fehlt: $source"
}

$pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$installDir = Join-Path $env:LOCALAPPDATA 'KAI\DeveloperHub'
$installedScript = Join-Path $installDir 'kai_dev_hub.py'
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $installedScript -Force

$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'KAI Developer Hub.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = ('"{0}" --repo "{1}" ui' -f $installedScript, $repositoryPath)
$shortcut.WorkingDirectory = $repositoryPath
$shortcut.Description = 'KAI: OpenCode/Hermes/Kimi mit lokaler und LiteLLM-Reserve'
$icon = Join-Path $env:USERPROFILE 'OneDrive\Pictures\kai-mark-light.ico'
if (Test-Path -LiteralPath $icon) {
    $shortcut.IconLocation = "$icon,0"
}
$shortcut.Save()

Write-Output "INSTALLED_SCRIPT=$installedScript"
Write-Output "DESKTOP_SHORTCUT=$shortcutPath"
Write-Output "REPOSITORY=$repositoryPath"
