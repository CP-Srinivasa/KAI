param(
    [string]$FeedUrl = "https://cointelegraph.com/rss",
    [string]$SourceId = "cointelegraph",
    [string]$SourceName = "CoinTelegraph",
    [string]$SourcesConfigPath = "",
    [switch]$SingleSourceOnly,
    [int]$TopN = 5,
    [int]$PendingAnnotationLimit = 20,
    [double]$AutoCheckThresholdPct = 5.0,
    [int]$AutoCheckHorizonHours = 24,
    [double]$AutoCheckMinAgeHours = 24.0,
    [int]$AutoCheckTimeoutSeconds = 10,
    [switch]$ApplyAutoCheck,
    [switch]$DryRunAutoCheck,
    [string]$ArtifactsDir = "artifacts",
    [string]$RelayEndpoint = "",
    [int]$RelayBatchSize = 100,
    [int]$RelayTimeoutSeconds = 10,
    [int]$RelayMaxAttempts = 3,
    [int]$SignalStatusLookbackHours = 24,
    [switch]$SkipExchangeRelay,
    [switch]$DryOps
)

$ErrorActionPreference = "Stop"

function Get-JsonlLineCount {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return 0
    }
    $lines = Get-Content $Path -Encoding UTF8
    return @($lines).Count
}

function New-RetentionSnapshot {
    param([string]$ArtifactsPath)

    $trackedFiles = @(
        "alert_audit.jsonl",
        "alert_outcomes.jsonl",
        "trading_loop_audit.jsonl",
        "paper_execution_audit.jsonl"
    )

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupRoot = Join-Path $ArtifactsPath "retention_backups"
    $snapshotDir = Join-Path $backupRoot "snapshot_$timestamp"
    New-Item -ItemType Directory -Path $snapshotDir -Force | Out-Null

    $state = @{}
    foreach ($name in $trackedFiles) {
        $path = Join-Path $ArtifactsPath $name
        $count = Get-JsonlLineCount -Path $path
        $state[$name] = @{
            path = $path
            before_count = $count
        }
        if (Test-Path $path) {
            Copy-Item -Path $path -Destination (Join-Path $snapshotDir $name) -Force
        }
    }

    return @{
        snapshot_dir = $snapshotDir
        state = $state
    }
}

function Assert-RetentionGuard {
    param(
        [hashtable]$RetentionSnapshot
    )

    foreach ($entry in $RetentionSnapshot.state.GetEnumerator()) {
        $name = $entry.Key
        $meta = $entry.Value
        $afterCount = Get-JsonlLineCount -Path $meta.path
        if ($afterCount -lt $meta.before_count) {
            Write-Host "[guard] Retention guard violation for $name" -ForegroundColor Red
            Write-Host "        before=$($meta.before_count) after=$afterCount" -ForegroundColor Red
            Write-Host "        snapshot=$($RetentionSnapshot.snapshot_dir)" -ForegroundColor Red
            exit 3
        }
    }
}

function Get-ActiveRssSources {
    $pyCode = @'
import asyncio
import json
from app.core.enums import SourceStatus, SourceType
from app.core.settings import get_settings
from app.storage.db.session import build_session_factory
from app.storage.repositories.source_repo import SourceRepository

async def main() -> None:
    settings = get_settings()
    session_factory = build_session_factory(settings.db)
    async with session_factory.begin() as session:
        repo = SourceRepository(session)
        rows = await repo.list(
            source_type=SourceType.RSS_FEED,
            status=SourceStatus.ACTIVE,
        )
    result = []
    for row in rows:
        url = (row.normalized_url or row.original_url or '').strip()
        if not url:
            continue
        source_id = (row.provider or row.source_id or 'rss_feed').strip()
        source_name = (row.provider or 'RSS Feed').strip()
        result.append(
            {
                "url": url,
                "source_id": source_id,
                "source_name": source_name,
            }
        )
    print(json.dumps(result))

asyncio.run(main())
'@

    try {
        $json = python -c $pyCode
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($json)) {
            return @()
        }
        $parsed = $json | ConvertFrom-Json
        return @($parsed)
    } catch {
        Write-Host "[plan] Active-source lookup failed, fallback to single-source." -ForegroundColor Yellow
        return @()
    }
}

function Resolve-SourcesFromConfig {
    param([string]$ConfigPath)

    if ([string]::IsNullOrWhiteSpace($ConfigPath) -or -not (Test-Path $ConfigPath)) {
        return @()
    }

    try {
        $raw = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Write-Host "[plan] Invalid source config JSON: $ConfigPath" -ForegroundColor Yellow
        return @()
    }

    $items = @()
    if ($raw -and $raw.PSObject.Properties.Name -contains "sources") {
        $items = @($raw.sources)
    } else {
        $items = @($raw)
    }

    $result = @()
    foreach ($item in $items) {
        if ($null -eq $item) { continue }
        $url = [string]$item.url
        if ([string]::IsNullOrWhiteSpace($url)) { continue }
        $sid = [string]$item.source_id
        $sname = [string]$item.source_name
        if ([string]::IsNullOrWhiteSpace($sid)) { $sid = $SourceId }
        if ([string]::IsNullOrWhiteSpace($sname)) { $sname = $SourceName }
        $result += [pscustomobject]@{
            url = $url.Trim()
            source_id = $sid.Trim()
            source_name = $sname.Trim()
        }
    }
    return $result
}

$autoCheckMode = if ($DryRunAutoCheck) { "--dry-run" } else { "--apply" }
if ($ApplyAutoCheck) {
    # Backward-compatible explicit apply switch.
    $autoCheckMode = "--apply"
}
Write-Host "[plan] Auto-check mode: $($autoCheckMode.TrimStart('-'))" -ForegroundColor Cyan

$sources = @()
if (-not $SingleSourceOnly) {
    $sources = Resolve-SourcesFromConfig -ConfigPath $SourcesConfigPath
    if ($sources.Count -eq 0) {
        $sources = Get-ActiveRssSources
    }
}
if ($sources.Count -eq 0) {
    $sources = @(
        [pscustomobject]@{
            url = $FeedUrl
            source_id = $SourceId
            source_name = $SourceName
        }
    )
}

Write-Host "[plan] Source plan: $($sources.Count) source(s)" -ForegroundColor Cyan
foreach ($src in $sources) {
    Write-Host "      - $($src.source_id): $($src.url)" -ForegroundColor DarkCyan
}

$retentionSnapshot = New-RetentionSnapshot -ArtifactsPath $ArtifactsDir
Write-Host "[guard] Snapshot created: $($retentionSnapshot.snapshot_dir)" -ForegroundColor DarkGray

if (-not $DryOps) {
    Write-Host "[1/6] Running pipeline across sources..." -ForegroundColor Cyan
    $index = 0
    foreach ($src in $sources) {
        $index += 1
        Write-Host "      [$index/$($sources.Count)] pipeline-run $($src.url)" -ForegroundColor DarkCyan
        python -m app.cli.main pipeline-run $src.url --source-id $src.source_id --source-name $src.source_name --top-n $TopN
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    Write-Host "[2/6] Running auto-check..." -ForegroundColor Cyan
    python -m app.cli.main alerts auto-check `
        --threshold-pct $AutoCheckThresholdPct `
        --horizon-hours $AutoCheckHorizonHours `
        --min-age-hours $AutoCheckMinAgeHours `
        --timeout-seconds $AutoCheckTimeoutSeconds `
        --artifacts-dir $ArtifactsDir `
        $autoCheckMode
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[3/6] Writing PH5 hold report..." -ForegroundColor Cyan
    python -m app.cli.main alerts hold-report --artifacts-dir $ArtifactsDir --output-dir "$ArtifactsDir/ph5_hold"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[4/6] Listing pending annotations..." -ForegroundColor Cyan
    python -m app.cli.main alerts pending-annotations --limit $PendingAnnotationLimit --artifacts-dir $ArtifactsDir
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
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Assert-RetentionGuard -RetentionSnapshot $retentionSnapshot
Write-Host "[guard] Retention guard passed." -ForegroundColor Green

exit 0
