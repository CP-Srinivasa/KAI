<#
  Paper Trading Cron — runs BTC/USDT + ETH/USDT cycles every invocation.
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

# ── Install mode ─────────────────────────────────────────────────────────────
if ($Install) {
    $scriptPath = Join-Path $ProjectRoot "scripts\paper_trading_cron.ps1"
    $action = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

    # Delete existing task if present
    schtasks /Delete /TN "KAI-PaperTrading" /F 2>$null

    # Create: every 10 minutes, indefinitely, start now
    schtasks /Create `
        /TN "KAI-PaperTrading" `
        /TR $action `
        /SC MINUTE /MO 10 `
        /ST (Get-Date -Format "HH:mm") `
        /F

    if ($LASTEXITCODE -eq 0) {
        Write-Host "Task 'KAI-PaperTrading' installed (every 10 min)."
        Write-Host "View:   schtasks /Query /TN KAI-PaperTrading"
        Write-Host "Delete: schtasks /Delete /TN KAI-PaperTrading /F"
    } else {
        Write-Host "ERROR: Failed to create scheduled task." -ForegroundColor Red
    }
    exit
}

# ── Helpers ──────────────────────────────────────────────────────────────────
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

# ── Main ─────────────────────────────────────────────────────────────────────
Set-Location $ProjectRoot

Write-Log "--- cron start ---"
Run-Cycle "BTC/USDT"
Start-Sleep -Seconds 15
Run-Cycle "ETH/USDT"
Write-Log "--- cron end ---"
