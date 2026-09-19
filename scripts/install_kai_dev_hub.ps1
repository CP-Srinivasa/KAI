[CmdletBinding()]
param(
    [string]$Repository,
    [switch]$SkipScheduledTask
)

$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$primary = (& git -C $sourceRoot worktree list --porcelain | Select-Object -First 1)
if (-not $primary -or -not $primary.StartsWith('worktree ')) {
    throw 'Kanonischer KAI-Checkout konnte nicht aufgelöst werden.'
}
if (-not $Repository) {
    $Repository = $primary.Substring(9)
}
$repositoryPath = (Resolve-Path -LiteralPath $Repository).Path
foreach ($required in @('AGENTS.md', 'docs\AI_HANDOFF.md', 'opencode.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $repositoryPath $required))) {
        throw "KAI-Checkout ist unvollständig: $required"
    }
}

$source = Join-Path $PSScriptRoot 'kai_dev_hub.py'
$sourceWorkflow = Join-Path $PSScriptRoot 'kai_dev_workflow.py'
foreach ($file in @($source, $sourceWorkflow)) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Hub-Quelldatei fehlt: $file" }
}

$pythonExe = (& python -c 'import sys; print(sys.executable)').Trim()
if (-not $pythonExe) { throw 'Python-Interpreter konnte nicht aufgelöst werden.' }
$pythonw = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "pythonw.exe fehlt neben dem aktiven Interpreter: $pythonw"
}
$version = (& $pythonExe $source --version).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "Ungültige Hub-Version: $version" }
$installDir = Join-Path $env:USERPROFILE ('.kai\developer-hub\app\v' + $version)
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
$installedScript = Join-Path $installDir 'kai_dev_hub.py'
$installedWorkflow = Join-Path $installDir 'kai_dev_workflow.py'
Copy-Item -LiteralPath $source -Destination $installedScript -Force
Copy-Item -LiteralPath $sourceWorkflow -Destination $installedWorkflow -Force

$head = (& git -C $sourceRoot rev-parse HEAD).Trim()
$manifest = [ordered]@{
    schema_version = 1
    hub_version = $version
    installed_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    source_head = $head
    source_branch = (& git -C $sourceRoot branch --show-current).Trim()
    repository = $repositoryPath
    hub_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $installedScript).Hash
    workflow_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $installedWorkflow).Hash
}
$manifestPath = Join-Path $installDir 'install.json'
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding utf8

$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'KAI Developer Hub.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = ('"{0}" --repo "{1}" ui' -f $installedScript, $repositoryPath)
$shortcut.WorkingDirectory = $repositoryPath
$shortcut.Description = "KAI Developer Hub $version - OpenCode/Hermes/Kimi"
$icon = Join-Path $env:USERPROFILE 'OneDrive\Pictures\kai-mark-light.ico'
if (Test-Path -LiteralPath $icon) { $shortcut.IconLocation = "$icon,0" }
$shortcut.Save()

$taskName = 'KAI-Developer-Reserve-Health'
if (-not $SkipScheduledTask) {
    $healthFile = Join-Path $env:USERPROFILE '.kai\developer-hub\health\last.json'
    $arguments = ('"{0}" --repo "{1}" doctor --mode offline --output "{2}"' -f $installedScript, $repositoryPath, $healthFile)
    $taskAction = New-ScheduledTaskAction -Execute $pythonExe -Argument $arguments -WorkingDirectory $repositoryPath
    $taskTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
        -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
        -LogonType Interactive -RunLevel Limited
    $taskSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
        -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTrigger `
        -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null
}

Write-Output "HUB_VERSION=$version"
Write-Output "INSTALLED_SCRIPT=$installedScript"
Write-Output "INSTALL_MANIFEST=$manifestPath"
Write-Output "DESKTOP_SHORTCUT=$shortcutPath"
Write-Output "REPOSITORY=$repositoryPath"
Write-Output "LOCAL_HEALTH_TASK=$($taskName):$(-not $SkipScheduledTask)"
