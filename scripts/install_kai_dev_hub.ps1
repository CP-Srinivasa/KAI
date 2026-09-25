[CmdletBinding()]
param(
    [string]$Repository,
    [switch]$SkipScheduledTask,
    [switch]$SkipShortcut,
    [string]$InstallRoot,
    [string]$PythonExe
)

# Reihenfolge ist Vertrag (K7, Befund 24.09.2026): ERST alles aufloesen und
# pruefen, DANN schreiben. Frueher brach der Installer bei detached HEAD an
# `(git branch --show-current).Trim()` ab, nachdem die Hub-Dateien schon
# kopiert waren -- zurueck blieb ein halber Versionsordner ohne install.json.

$ErrorActionPreference = 'Stop'

function Get-GitText {
    # Null-sicher: leere Ausgabe wird '' statt $null. Exit-Code != 0 wirft.
    param([Parameter(Mandatory)][string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    $out = & git @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) { throw "git $($Arguments -join ' ') scheiterte (Exit $LASTEXITCODE)" }
    return "$(@($out) -join "`n")".Trim()
}

# --- 1. Aufloesen und pruefen (keine Seiteneffekte) --------------------------

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:USERPROFILE '.kai\developer-hub' }
$sourceRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$primary = (Get-GitText @('-C', $sourceRoot, 'worktree', 'list', '--porcelain')).Split("`n")[0]
if (-not $primary.StartsWith('worktree ')) {
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

if (-not $PythonExe) {
    $PythonExe = "$(& python -c 'import sys; print(sys.executable)')".Trim()
}
if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python-Interpreter konnte nicht aufgelöst werden: '$PythonExe'"
}
$pythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
$pythonw = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
if (-not $SkipShortcut -and -not (Test-Path -LiteralPath $pythonw)) {
    throw "pythonw.exe fehlt neben dem aktiven Interpreter: $pythonw"
}
$version = "$(& $pythonExe $source --version)".Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "Ungültige Hub-Version: $version" }

$head = Get-GitText @('-C', $sourceRoot, 'rev-parse', 'HEAD')
if ($head -notmatch '^[0-9a-f]{40}$') { throw "Quell-HEAD ist kein voller Commit-SHA: '$head'" }
# Detached HEAD ist zulaessig: dann gibt es keinen Branchnamen. Die Herkunft
# belegt `source_head`, nicht der Name.
$branch = Get-GitText @('-C', $sourceRoot, 'branch', '--show-current')
$detached = -not $branch

# --- 2. Schreiben: Staging, dann Umbenennen ----------------------------------

$appDir = Join-Path $InstallRoot 'app'
$installDir = Join-Path $appDir ('v' + $version)
$suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)
$staging = Join-Path $appDir ('.staging-v' + $version + '-' + $suffix)
$replaced = Join-Path $appDir ('.replaced-v' + $version + '-' + $suffix)

New-Item -ItemType Directory -Path $appDir -Force | Out-Null
try {
    New-Item -ItemType Directory -Path $staging | Out-Null
    $stagedScript = Join-Path $staging 'kai_dev_hub.py'
    $stagedWorkflow = Join-Path $staging 'kai_dev_workflow.py'
    Copy-Item -LiteralPath $source -Destination $stagedScript
    Copy-Item -LiteralPath $sourceWorkflow -Destination $stagedWorkflow

    $manifest = [ordered]@{
        schema_version = 1
        hub_version = $version
        installed_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        source_head = $head
        source_branch = $(if ($detached) { $null } else { $branch })
        source_detached = [bool]$detached
        repository = $repositoryPath
        hub_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $stagedScript).Hash
        workflow_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $stagedWorkflow).Hash
    }
    # UTF-8 ohne BOM, unabhaengig davon, ob powershell.exe oder pwsh laeuft.
    [System.IO.File]::WriteAllText(
        (Join-Path $staging 'install.json'),
        ($manifest | ConvertTo-Json -Depth 4),
        (New-Object System.Text.UTF8Encoding($false)))

    if (Test-Path -LiteralPath $installDir) {
        try {
            [System.IO.Directory]::Move($installDir, $replaced)
        } catch {
            throw "Installation abgebrochen: $installDir ist gesperrt. Hub zuerst schließen. ($($_.Exception.Message))"
        }
    }
    try {
        [System.IO.Directory]::Move($staging, $installDir)
    } catch {
        if ((Test-Path -LiteralPath $replaced) -and -not (Test-Path -LiteralPath $installDir)) {
            [System.IO.Directory]::Move($replaced, $installDir)
        }
        throw
    }
} catch {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    throw
}
if (Test-Path -LiteralPath $replaced) { Remove-Item -LiteralPath $replaced -Recurse -Force }

$installedScript = Join-Path $installDir 'kai_dev_hub.py'
$manifestPath = Join-Path $installDir 'install.json'

# --- 3. Shortcut und Health-Task (abschaltbar, z. B. fuer Tests) -------------

$shortcutPath = ''
if (-not $SkipShortcut) {
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
}

$taskName = 'KAI-Developer-Reserve-Health'
if (-not $SkipScheduledTask) {
    $healthFile = Join-Path $InstallRoot 'health\last.json'
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
Write-Output "SOURCE_HEAD=$head"
Write-Output "SOURCE_DETACHED=$detached"
Write-Output "DESKTOP_SHORTCUT=$(if ($SkipShortcut) { 'skipped' } else { $shortcutPath })"
Write-Output "REPOSITORY=$repositoryPath"
Write-Output "LOCAL_HEALTH_TASK=$($taskName):$(-not $SkipScheduledTask)"
