# kai-ln-budget - Budget von KAIs Sende-Account (litd) anzeigen oder setzen. NUR vom Laptop.
#
# Runbook docs/runbooks/ln_kai_account_budget.md Abschnitt 7a: Das Budget begrenzt am NODE, was KAIs
# Sendeschluessel ausgeben kann. Aendern darf es nur der Laptop (SSH-Schluessel admin@Node liegt
# nur hier). Die Pi hat keinen Zugang zu litd und darf das Budget nie setzen koennen.
#
#   kai-ln-budget            Stand zeigen (wie "show")
#   kai-ln-budget show       Restbudget, Startbudget, Zahlungen
#   kai-ln-budget 5000       Restbudget auf 5000 sat setzen (auffuellen oder senken)
#   kai-ln-budget 200000 -Force   ueber der Sicherheitsgrenze von 100000 sat
#
# Jede Aenderung wird mit Zeit, altem und neuem Wert protokolliert:
#   %USERPROFILE%\.kai\logs\ln_budget.log
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Action = 'show',
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$Node = 'admin@192.168.178.51'
$Label = 'kai'
$MaxWithoutForce = 100000
$LogFile = Join-Path $env:USERPROFILE '.kai\logs\ln_budget.log'

function Get-KaiAccount {
    $raw = ssh -o ConnectTimeout=10 $Node "sudo -u lit litcli accounts info --label $Label"
    if ($LASTEXITCODE -ne 0) { throw "litd nicht erreichbar oder Account '$Label' fehlt (rc=$LASTEXITCODE)" }
    return (($raw | Out-String) | ConvertFrom-Json)
}

function Show-KaiAccount($acct) {
    $paid = @($acct.payments | Where-Object { $_.state -eq 'SUCCEEDED' })
    $spent = 0
    foreach ($p in $paid) { $spent += [int64]$p.full_amount }
    Write-Host ("Account {0} ({1})" -f $acct.label, $acct.id)
    Write-Host ("  Restbudget:   {0} sat" -f $acct.current_balance)
    Write-Host ("  Startbudget:  {0} sat" -f $acct.initial_balance)
    Write-Host ("  Zahlungen:    {0} erfolgreich, zusammen {1} sat (inkl. Gebuehren)" -f $paid.Count, $spent)
    $when = [DateTimeOffset]::FromUnixTimeSeconds([int64]$acct.last_update).UtcDateTime
    Write-Host ("  Letzte Aenderung: {0:yyyy-MM-dd HH:mm}Z" -f $when)
}

$before = Get-KaiAccount
if ($Action -eq 'show') {
    Show-KaiAccount $before
    exit 0
}

$newBalance = 0
if (-not [int64]::TryParse($Action, [ref]$newBalance) -or $newBalance -lt 0) {
    throw "Aufruf: kai-ln-budget [show | <sat>]  - '$Action' ist keine Zahl >= 0"
}
if ($newBalance -gt $MaxWithoutForce -and -not $Force) {
    throw "$newBalance sat liegt ueber $MaxWithoutForce sat. Tippfehler? Sonst mit -Force wiederholen."
}

$old = [int64]$before.current_balance
ssh -o ConnectTimeout=10 $Node "sudo -u lit litcli accounts update --label $Label --new_balance $newBalance" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Setzen fehlgeschlagen (rc=$LASTEXITCODE) - Budget unveraendert $old sat" }

$after = Get-KaiAccount
if ([int64]$after.current_balance -ne $newBalance) {
    throw "Gegenlesen weicht ab: gesetzt $newBalance, gelesen $($after.current_balance) sat"
}
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
$line = '{0:yyyy-MM-ddTHH:mm:ssZ} {1} {2} -> {3} sat ({4})' -f (Get-Date).ToUniversalTime(), $Label, $old, $newBalance, $env:USERNAME
Add-Content -Path $LogFile -Value $line -Encoding UTF8
Write-Host ("Budget gesetzt: {0} -> {1} sat (Log: {2})" -f $old, $newBalance, $LogFile)
Show-KaiAccount $after
