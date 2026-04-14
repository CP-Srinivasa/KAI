<#
  Paper Trading Cron - runs BTC/USDT + ETH/USDT cycles every invocation.
  Scheduled via Windows Task Scheduler (every 10 minutes).

  Usage (manual):  powershell -ExecutionPolicy Bypass -File scripts\paper_trading_cron.ps1

  Install task:    Run the schtasks command at the bottom of this file, or:
                   scripts\paper_trading_cron.ps1 -Install
  Remove task:     schtasks /Delete /TN "KAI-PaperTrading" /F
#>

param(
    [switch]$Install
)

$ErrorActionPreference = "Continue"
$ProjectRoot = "C:\Users\sasch\.local\bin\ai_analyst_trading_bot"
$LogFile = Join-Path $ProjectRoot "artifacts\paper_trading_cron.log"
$Python = "python"

# -- Install mode -------------------------------------------------------------
if ($Install) {
    $scriptPath = Join-Path $ProjectRoot "scripts\paper_trading_cron.ps1"
    $action = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

    # Delete existing task if present
    schtasks /Delete /TN "KAI-PaperTrading" /F 2>$null

    # Create: every 10 minutes, run whether user is logged on or not.
    # /RL HIGHEST = run with highest privileges (needed for wake-from-sleep)
    # /RU requires the current user's password (prompted by schtasks).
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    schtasks /Create `
        /TN "KAI-PaperTrading" `
        /TR $action `
        /SC MINUTE /MO 10 `
        /ST (Get-Date -Format "HH:mm") `
        /RU $currentUser `
        /RL HIGHEST `
        /F

    if ($LASTEXITCODE -eq 0) {
        Write-Host "Task 'KAI-PaperTrading' installed (every 10 min, runs whether logged on or not)."
        Write-Host ""
        Write-Host "IMPORTANT: To enable wake-from-sleep, open Task Scheduler GUI:"
        Write-Host "  1. Open taskschd.msc"
        Write-Host "  2. Find KAI-PaperTrading"
        Write-Host "  3. Properties > Conditions > check 'Wake the computer to run this task'"
        Write-Host "  4. Properties > Settings > check 'Run task as soon as possible after a scheduled start is missed'"
        Write-Host ""
        Write-Host "View:   schtasks /Query /TN KAI-PaperTrading"
        Write-Host "Delete: schtasks /Delete /TN KAI-PaperTrading /F"
    } else {
        Write-Host "ERROR: Failed to create scheduled task." -ForegroundColor Red
        Write-Host "Try running this script as Administrator." -ForegroundColor Yellow
    }
    exit
}

# -- Helpers ------------------------------------------------------------------
function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $msg" | Out-File -Append -Encoding utf8 $LogFile
}

function Run-Cycle($symbol) {
    try {
        $output = & $Python -m app.cli.main trading run-once `
            --symbol $symbol `
            --mode paper `
            --provider coingecko `
            --analysis-profile conservative 2>&1 | Out-String

        # Extract key fields
        $cycleId = if ($output -match "cycle_id=(\S+)") { $Matches[1] } else { "unknown" }
        $status  = if ($output -match "status=(\S+)")   { $Matches[1] } else { "unknown" }
        $fill    = if ($output -match "fill_simulated=(\S+)") { $Matches[1] } else { "unknown" }

        Write-Log "$symbol  cycle=$cycleId  status=$status  fill=$fill"
    } catch {
        Write-Log "$symbol  ERROR: $_"
    }
}

# -- Server watchdog ---------------------------------------------------------
function Ensure-Server {
    try {
        $health = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" `
            -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        if ($health.StatusCode -eq 200) { return }
    } catch {}

    # Server is down - restart it.
    Write-Log "SERVER DOWN - restarting uvicorn"
    $serverLog = Join-Path $ProjectRoot "logs\server.log"
    Start-Process -NoNewWindow -FilePath $Python `
        -ArgumentList "-m", "uvicorn", "app.api.main:app", `
            "--host", "127.0.0.1", "--port", "8000", "--log-level", "info" `
        -RedirectStandardOutput $serverLog `
        -RedirectStandardError (Join-Path $ProjectRoot "logs\server.err.log")

    Start-Sleep -Seconds 5
    try {
        $health = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" `
            -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            Write-Log "SERVER restarted OK"
        } else {
            Write-Log "SERVER restart FAILED (status $($health.StatusCode))"
        }
    } catch {
        Write-Log "SERVER restart FAILED: $_"
    }
}

# -- Main ---------------------------------------------------------------------
Set-Location $ProjectRoot

Write-Log "--- cron start ---"

# Ensure the FastAPI server (+ Telegram bot poller) is running.
Ensure-Server

Run-Cycle "BTC/USDT"
Start-Sleep -Seconds 15
Run-Cycle "ETH/USDT"

# Auto-annotate pending directional alerts (every 6th run = ~hourly)
$marker = Join-Path $ProjectRoot "artifacts\.annotate_counter"
$counter = 0
if (Test-Path $marker) { $counter = [int](Get-Content $marker -ErrorAction SilentlyContinue) }
$counter++
if ($counter -ge 6) {
    $counter = 0
    Write-Log "auto-annotate starting"
    try {
        $output = & $Python -m app.cli.main alerts auto-annotate 2>&1 | Out-String
        $annotated = if ($output -match "(\d+) annotated") { $Matches[1] } else { "0" }
        Write-Log "auto-annotate done: $annotated annotations"
    } catch {
        Write-Log "auto-annotate ERROR: $_"
    }
}
$counter | Out-File -Encoding utf8 $marker

# Daily briefing + health check (once per day, first run after 07:50)
$hour = (Get-Date).Hour
$minute = (Get-Date).Minute
$briefingMarker = Join-Path $ProjectRoot "artifacts\.briefing_date"
$today = Get-Date -Format "yyyy-MM-dd"
$lastBriefing = if (Test-Path $briefingMarker) { Get-Content $briefingMarker -ErrorAction SilentlyContinue } else { "" }
if ($hour -ge 8 -and $lastBriefing -ne $today) {
    Write-Log "daily-briefing starting"
    try {
        $briefing = & $Python -m app.cli.main alerts daily-briefing --notify 2>&1 | Out-String
        Write-Log "briefing:`n$briefing"
        $health = & $Python -m app.cli.main alerts health-check --notify 2>&1 | Out-String
        Write-Log "health-check: $($health.Trim())"
    } catch {
        Write-Log "daily-briefing ERROR: $_"
    }
    $today | Out-File -Encoding utf8 $briefingMarker
}

# Pipeline run-all (every 4th run = ~40 min, ingests all active RSS feeds)
$pipelineMarker = Join-Path $ProjectRoot "artifacts\.pipeline_counter"
$pipelineCounter = 0
if (Test-Path $pipelineMarker) { $pipelineCounter = [int](Get-Content $pipelineMarker -ErrorAction SilentlyContinue) }
$pipelineCounter++
if ($pipelineCounter -ge 4) {
    $pipelineCounter = 0
    Write-Log "pipeline run-all starting"
    try {
        $output = & $Python -m app.cli.main pipeline run-all --top-n 1 2>&1 | Out-String
        Write-Log "pipeline run-all done"
    } catch {
        Write-Log "pipeline run-all ERROR: $_"
    }
}
$pipelineCounter | Out-File -Encoding utf8 $pipelineMarker

Write-Log "--- cron end ---"
