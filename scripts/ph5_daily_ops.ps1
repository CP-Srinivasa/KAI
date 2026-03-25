param(
    [string]$FeedUrl = "https://cointelegraph.com/rss",
    [string]$SourceId = "cointelegraph",
    [string]$SourceName = "CoinTelegraph",
    [int]$TopN = 5,
    [int]$PendingAnnotationLimit = 20
)

Write-Host "[1/3] Running pipeline..." -ForegroundColor Cyan
python -m app.cli.main pipeline-run $FeedUrl --source-id $SourceId --source-name $SourceName --top-n $TopN
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[2/3] Writing PH5 hold report..." -ForegroundColor Cyan
python -m app.cli.main alerts hold-report
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[3/3] Listing pending annotations..." -ForegroundColor Cyan
python -m app.cli.main alerts pending-annotations --limit $PendingAnnotationLimit
exit $LASTEXITCODE
