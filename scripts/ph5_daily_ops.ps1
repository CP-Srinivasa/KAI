param(
    [string]$FeedUrl = "https://cointelegraph.com/rss",
    [string]$SourceId = "cointelegraph",
    [string]$SourceName = "CoinTelegraph",
    [int]$TopN = 5,
    [int]$PendingAnnotationLimit = 20,
    [double]$AutoCheckThresholdPct = 5.0,
    [int]$AutoCheckHorizonHours = 24,
    [double]$AutoCheckMinAgeHours = 24.0,
    [int]$AutoCheckTimeoutSeconds = 10,
    [switch]$ApplyAutoCheck,
    [string]$RelayEndpoint = "",
    [int]$RelayBatchSize = 100,
    [int]$RelayTimeoutSeconds = 10,
    [int]$RelayMaxAttempts = 3,
    [int]$SignalStatusLookbackHours = 24,
    [switch]$SkipExchangeRelay,
    [switch]$DryOps
)

if (-not $DryOps) {
    Write-Host "[1/6] Running pipeline..." -ForegroundColor Cyan
    python -m app.cli.main pipeline-run $FeedUrl --source-id $SourceId --source-name $SourceName --top-n $TopN
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[2/6] Running auto-check..." -ForegroundColor Cyan
    $autoCheckMode = if ($ApplyAutoCheck) { "--apply" } else { "--dry-run" }
    python -m app.cli.main alerts auto-check `
        --threshold-pct $AutoCheckThresholdPct `
        --horizon-hours $AutoCheckHorizonHours `
        --min-age-hours $AutoCheckMinAgeHours `
        --timeout-seconds $AutoCheckTimeoutSeconds `
        $autoCheckMode
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[3/6] Writing PH5 hold report..." -ForegroundColor Cyan
    python -m app.cli.main alerts hold-report
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[4/6] Listing pending annotations..." -ForegroundColor Cyan
    python -m app.cli.main alerts pending-annotations --limit $PendingAnnotationLimit
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "[mode] DryOps enabled: skipping pipeline, auto-check, hold-report, pending-annotations." -ForegroundColor Yellow
}

$relayStepLabel = if ($DryOps) { "[1/2]" } else { "[5/6]" }
$statusStepLabel = if ($DryOps) { "[2/2]" } else { "[6/6]" }

$effectiveRelayEndpoint = if (-not [string]::IsNullOrWhiteSpace($RelayEndpoint)) {
    $RelayEndpoint
} else {
    $env:OPERATOR_SIGNAL_EXCHANGE_RELAY_ENDPOINT
}

if ($SkipExchangeRelay) {
    Write-Host "$relayStepLabel Skipping exchange relay (--SkipExchangeRelay)." -ForegroundColor Yellow
} elseif ([string]::IsNullOrWhiteSpace($effectiveRelayEndpoint)) {
    Write-Host "$relayStepLabel Skipping exchange relay: no endpoint configured." -ForegroundColor Yellow
    Write-Host "      Set -RelayEndpoint or OPERATOR_SIGNAL_EXCHANGE_RELAY_ENDPOINT." -ForegroundColor Yellow
} else {
    Write-Host "$relayStepLabel Running exchange relay..." -ForegroundColor Cyan
    python -m app.cli.main alerts exchange-relay `
        --endpoint $effectiveRelayEndpoint `
        --batch-size $RelayBatchSize `
        --timeout-seconds $RelayTimeoutSeconds `
        --max-attempts $RelayMaxAttempts
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "$statusStepLabel Signal pipeline status (compact)..." -ForegroundColor Cyan
python -m app.cli.main alerts signal-status --lookback-hours $SignalStatusLookbackHours
exit $LASTEXITCODE
