<#
  KAI Entry Watch Task - high-frequency watch for pending operator entries.

  Usage (manual):  powershell -ExecutionPolicy Bypass -File scripts\entry_watch_task.ps1

  Install task:    scripts\entry_watch_task.ps1 -Install
  Remove task:     scripts\entry_watch_task.ps1 -Remove

  Safety:
  - fail-closed when EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED=false
  - paper/operator bridge only; live execution stays governed by settings
  - lock file prevents overlapping Task Scheduler invocations
#>

param(
    [switch]$Install,
    [switch]$Remove
)

$ErrorActionPreference = "Continue"
$ProjectRoot = "C:\Users\sasch\.local\bin\ai_analyst_trading_bot"
$LogFile = Join-Path $ProjectRoot "artifacts\entry_watch_task.log"
$LockFile = Join-Path $ProjectRoot "artifacts\.entry_watch_task.lock"
$Python = "python"
$TaskName = "KAI-EntryWatch"

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $msg" | Out-File -Append -Encoding utf8 $LogFile
}

if ($Remove) {
    schtasks /Delete /TN $TaskName /F 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Task '$TaskName' removed."
    } else {
        Write-Host "Task '$TaskName' was not installed or could not be removed."
    }
    exit
}

if ($Install) {
    $scriptPath = Join-Path $ProjectRoot "scripts\entry_watch_task.ps1"
    $action = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

    schtasks /Delete /TN $TaskName /F 2>$null
    schtasks /Create `
        /TN $TaskName `
        /TR $action `
        /SC MINUTE /MO 1 `
        /ST (Get-Date -Format "HH:mm") `
        /F

    if ($LASTEXITCODE -eq 0) {
        Write-Host "Task '$TaskName' installed (every 1 min, current user context)."
        Write-Host ""
        Write-Host "For true unattended 24/7 on Windows:"
        Write-Host "  1. Open taskschd.msc"
        Write-Host "  2. Find $TaskName"
        Write-Host "  3. Properties > General > select 'Run whether user is logged on or not'"
        Write-Host "  4. Properties > Conditions > check 'Wake the computer to run this task'"
        Write-Host "  5. Properties > Settings > prevent parallel instances / run missed task ASAP"
        Write-Host ""
        Write-Host "View:   schtasks /Query /TN $TaskName"
        Write-Host "Delete: powershell -ExecutionPolicy Bypass -File scripts\entry_watch_task.ps1 -Remove"
    } else {
        Write-Host "ERROR: Failed to create scheduled task." -ForegroundColor Red
        Write-Host "Try running PowerShell as Administrator." -ForegroundColor Yellow
    }
    exit
}

Set-Location $ProjectRoot
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null

if (Test-Path $LockFile) {
    $age = (Get-Date) - (Get-Item $LockFile).LastWriteTime
    if ($age.TotalMinutes -lt 5) {
        Write-Log "skip: previous entry-watch invocation still active"
        exit 0
    }
    Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
}

try {
    New-Item -Path $LockFile -ItemType File -ErrorAction Stop | Out-Null
} catch {
    Write-Log "skip: could not acquire lock"
    exit 0
}

try {
    $output = & $Python -m app.cli.main trading operator-signal-entry-watch `
        --duration-seconds 55 `
        --poll-interval-seconds 5 2>&1 | Out-String
    if ($output -match "enabled=False") {
        Write-Log "entry-watch disabled fail-closed"
        exit 0
    }
    $triggered = if ($output -match "triggered=(\S+)") { $Matches[1] } else { "0" }
    $filled    = if ($output -match "bridge_filled=(\S+)") { $Matches[1] } else { "0" }
    $held      = if ($output -match "held=(\S+)") { $Matches[1] } else { "0" }
    $stale     = if ($output -match "stale_or_unavailable=(\S+)") { $Matches[1] } else { "0" }
    Write-Log "entry-watch  triggered=$triggered  bridge_filled=$filled  held=$held  stale=$stale"
} catch {
    Write-Log "entry-watch ERROR: $_"
} finally {
    Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
}
