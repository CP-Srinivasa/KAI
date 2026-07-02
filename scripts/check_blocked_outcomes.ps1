# D-227 recall-proxy morning check. Runs the analysis on the Pi via SSH stdin,
# writes a timestamped report to KAI-mirror. Scheduled for 2026-05-30 ~08:00.
$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$outDir = "C:\Users\sasch\KAI-mirror"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Force $outDir | Out-Null }
$report = Join-Path $outDir "blocked_outcomes_report_$stamp.txt"
$script = "C:\Users\sasch\.local\bin\blocked_outcomes_analysis.py"

"=== blocked_outcomes recall-proxy report $stamp ===" | Out-File -FilePath $report -Encoding utf8
& ssh -o ConnectTimeout=15 ubuntu@192.168.178.23 'cd ~/ai_analyst_trading_bot && python3 -' < $script 2>&1 |
    Out-File -FilePath $report -Append -Encoding utf8
"---" | Out-File -FilePath $report -Append -Encoding utf8
"timer last/next:" | Out-File -FilePath $report -Append -Encoding utf8
& ssh -o ConnectTimeout=15 ubuntu@192.168.178.23 'systemctl list-timers kai-auto-annotate.timer --no-pager | grep kai-auto-annotate' 2>&1 |
    Out-File -FilePath $report -Append -Encoding utf8
