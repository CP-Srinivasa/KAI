<#
.SYNOPSIS
    Workstation-Skripte (Laptop) aus dem Repo an ihre Betriebsorte bringen -- oder
    nur berichten, wo Repo und Betrieb auseinanderlaufen.

.BESCHREIBUNG
    MindBlow 2.0, W0-8 (26.09.2026). Die Backup-/Vault-Skripte des Laptops lagen
    nur auf dem Laptop: nicht versioniert, nicht getestet, bei einem Laptop-Verlust
    nur noch im Vault zu finden. Sie leben jetzt in scripts/workstation/; die
    Scheduled Tasks rufen weiter die Betriebskopien unter %USERPROFILE%.

    Ohne -Apply: Bericht je Datei (SAME / DRIFT / MISSING) und Exit 3, wenn
    irgendetwas abweicht. Mit -Apply: vorhandene Betriebskopie nach
    <datei>.bak-<stempel> sichern, dann die Repo-Fassung kopieren; Exit 0, wenn
    danach alles SAME ist.

    Scheduled Tasks werden NICHT registriert -- das bleibt ein bewusster Schritt
    (tasks\*.xml, Register-ScheduledTask -Xml).

.PARAMETER HomeRoot
    Wurzel der Betriebsorte (Standard: %USERPROFILE%). Tests setzen ein Temp-Verzeichnis.
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$HomeRoot = $env:USERPROFILE
)
$ErrorActionPreference = 'Stop'

#: Repo-Datei (relativ zu scripts/workstation) -> Betriebsort (relativ zu HomeRoot).
$Map = [ordered]@{
    'kai_vault.ps1'                = 'KAI-mirror\scripts\kai_vault.ps1'
    'kai_vault_legacy_encrypt.ps1' = 'KAI-mirror\scripts\kai_vault_legacy_encrypt.ps1'
    'restore_test.ps1'             = '.local\bin\restore_test.ps1'
    'sync-memory.ps1'              = 'KAI-mirror\sync-memory.ps1'
    'mirror_backups_offsite.ps1'   = '.local\bin\mirror_backups_offsite.ps1'
    'tasks\KAI-Vault-OnAttach.xml' = 'KAI-mirror\scripts\tasks\KAI-Vault-OnAttach.xml'
    'kai_session_lagebild.py'      = 'KAI-mirror\scripts\kai_session_lagebild.py'
    'kai_claim.py'                 = 'KAI-mirror\scripts\kai_claim.py'
}

function Get-Sha([string]$p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }

$stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
$drift = 0
foreach ($rel in $Map.Keys) {
    $src = Join-Path $PSScriptRoot $rel
    $dst = Join-Path $HomeRoot $Map[$rel]
    if (-not (Test-Path -LiteralPath $src)) { throw "Repo-Datei fehlt: $src" }
    $state = if (-not (Test-Path -LiteralPath $dst)) { 'MISSING' }
             elseif ((Get-Sha $src) -eq (Get-Sha $dst)) { 'SAME' }
             else { 'DRIFT' }
    if ($Apply -and $state -ne 'SAME') {
        New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
        if ($state -eq 'DRIFT') { Copy-Item -LiteralPath $dst -Destination "$dst.bak-$stamp" -Force }
        Copy-Item -LiteralPath $src -Destination $dst -Force
        $state = if ((Get-Sha $src) -eq (Get-Sha $dst)) { "INSTALLED(war $state)" } else { 'FAILED' }
    }
    if ($state -notin 'SAME' -and $state -notlike 'INSTALLED*') { $drift++ }
    Write-Output ('{0,-22} {1,-30} -> {2}' -f $state, $rel, $dst)
}
if ($drift -gt 0) {
    Write-Output "$drift Datei(en) weichen ab$(if (-not $Apply) { ' -- mit -Apply installieren' })."
    exit 3
}
exit 0
