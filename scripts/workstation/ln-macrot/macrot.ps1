# macrot.ps1 -- Neuaufbau aller lnd-Macaroons, Laptop-Seite (erstmals 29.09.2026, D-294).
#
# Betriebskopie: %USERPROFILE%\KAI-mirror\scripts\ln-macrot\ (install_workstation.ps1).
# Voraussetzungen, Rechte und Versionsbindung: Runbook docs/runbooks/ln_kai_account_budget.md §11.
#
# Reihenfolge (jeweils ein Aufruf, erst weiter, wenn der vorige sauber endet):
#   1. .\macrot.ps1 pruefen      nur lesen: Abbruchbedingungen am Node, Zahlungs-Drain auf der Pi
#   2. .\macrot.ps1 rotieren     frische SCB holen, dann Neuaufbau am Node (Passwort C!)
#   3. .\macrot.ps1 pi           neue KAI-Schluessel Node -> Laptop -> Pi, einsetzen, beweisen
#   4. .\macrot.ps1 aufraeumen   alten Schluesselstand auf Node und Pi endgueltig vernichten
# Bei Problemen:
#   .\macrot.ps1 weiter         Node-Lauf nach Unterbrechung fortsetzen
#   .\macrot.ps1 zurueck        alten Stand auf Node UND Pi zuruecklegen (bis Schritt 4 moeglich)
#
# Kein Schritt enthaelt einen Sende-, Kanal- oder On-Chain-Befehl.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('pruefen', 'rotieren', 'pi', 'aufraeumen', 'weiter', 'zurueck')]
    [string]$Schritt
)
$ErrorActionPreference = 'Stop'
$Node = 'admin@192.168.178.51'
$Pi = 'ubuntu@192.168.178.23'
$Here = $PSScriptRoot
$Tmp = Join-Path $env:TEMP 'kai-macrot'
$ScbSync = Join-Path $env:USERPROFILE 'KAI-mirror\sync-scb-from-node.ps1'

function Run([string]$What, [scriptblock]$Cmd) {
    & $Cmd
    if ($LASTEXITCODE -ne 0) { throw "ABBRUCH bei: $What (Exit $LASTEXITCODE)" }
}

function Send-NodeScripts {
    Run 'Node-Arbeitsordner' { ssh $Node 'mkdir -p ~/kai-macrot && chmod 700 ~/kai-macrot' }
    Run 'Skripte zum Node' { scp -q "$Here\macrot_node.sh" "$Here\macrot_snap.py" "$Here\mac_ops.py" "${Node}:kai-macrot/" }
}

function Send-PiScripts {
    Run 'Pi-Arbeitsordner' { ssh $Pi 'mkdir -p ~/kai-macrot && chmod 700 ~/kai-macrot' }
    Run 'Skripte zur Pi' { scp -q "$Here\macrot_pi.sh" "$Here\mac_ops.py" "${Pi}:kai-macrot/" }
}

function Test-PiDrain {
    Run 'Zahlungs-Drain auf der Pi' {
        ssh $Pi 'cd ~/ai_analyst_trading_bot && ./.venv/bin/python -m scripts.payment_drain_check --journal artifacts/payments/payment_journal.jsonl'
    }
}

switch ($Schritt) {
    'pruefen' {
        Send-NodeScripts
        Run 'Abbruchbedingungen am Node' { ssh $Node 'bash ~/kai-macrot/macrot_node.sh check' }
        Test-PiDrain
        Write-Host "`nPRUEFEN: GO -- naechster Schritt: .\macrot.ps1 rotieren" -ForegroundColor Green
    }
    'rotieren' {
        Test-PiDrain
        Write-Host '== frische SCB vom Node holen (sync-scb-from-node.ps1)'
        if (-not (Test-Path -LiteralPath $ScbSync)) { throw "ABBRUCH: $ScbSync fehlt -- ohne frische SCB kein Umbau" }
        & powershell -NoProfile -ExecutionPolicy Bypass -File $ScbSync
        if ($LASTEXITCODE -ne 0) { throw 'ABBRUCH: SCB-Kopie nicht aktuell -- erst klaeren' }
        Write-Host '   SCB aktuell und geprueft.'
        Send-NodeScripts
        ssh -t $Node 'bash ~/kai-macrot/macrot_node.sh rotate'
        if ($LASTEXITCODE -ne 0) { throw 'Node-Lauf nicht sauber beendet -- Hinweise oben lesen (weiter/zurueck)' }
        Write-Host "`nNaechster Schritt: .\macrot.ps1 pi" -ForegroundColor Green
    }
    'weiter' {
        Send-NodeScripts
        ssh -t $Node 'bash ~/kai-macrot/macrot_node.sh resume'
        if ($LASTEXITCODE -ne 0) { throw 'Node-Lauf nicht sauber beendet' }
        Write-Host "`nNaechster Schritt: .\macrot.ps1 pi" -ForegroundColor Green
    }
    'pi' {
        Send-PiScripts
        if (Test-Path $Tmp) { Remove-Item -Recurse -Force $Tmp }
        New-Item -ItemType Directory -Path $Tmp | Out-Null
        try {
            Run 'Transfer Node -> Laptop' { scp -q "${Node}:kai-macrot/out/*" "$Tmp\" }
            Run 'incoming anlegen' { ssh $Pi 'mkdir -p ~/kai-secrets/lnd/incoming && chmod 700 ~/kai-secrets/lnd/incoming' }
            Run 'Transfer Laptop -> Pi' { scp -q "$Tmp\*" "${Pi}:kai-secrets/lnd/incoming/" }
        }
        finally {
            Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
        }
        Run 'Einsetzen auf der Pi' { ssh $Pi 'bash ~/kai-macrot/macrot_pi.sh install' }
        Run 'Beweis auf der Pi' { ssh $Pi 'bash ~/kai-macrot/macrot_pi.sh verify' }
        Write-Host "`nPI: FERTIG -- Budget pruefen mit: kai-ln-budget show" -ForegroundColor Green
        Write-Host 'Danach: .\macrot.ps1 aufraeumen' -ForegroundColor Green
    }
    'aufraeumen' {
        ssh -t $Node 'bash ~/kai-macrot/macrot_node.sh cleanup'
        if ($LASTEXITCODE -ne 0) { throw 'Node-Aufraeumen nicht sauber' }
        Run 'Pi aufraeumen' { ssh $Pi 'bash ~/kai-macrot/macrot_pi.sh cleanup' }
        Write-Host "`nAUFGERAEUMT -- alte Schluessel sind nirgends mehr gueltig." -ForegroundColor Green
    }
    'zurueck' {
        ssh -t $Node 'bash ~/kai-macrot/macrot_node.sh rollback'
        if ($LASTEXITCODE -ne 0) { throw 'Node-Rollback nicht sauber' }
        Run 'Pi zuruecklegen' { ssh $Pi 'bash ~/kai-macrot/macrot_pi.sh rollback' }
    }
}
