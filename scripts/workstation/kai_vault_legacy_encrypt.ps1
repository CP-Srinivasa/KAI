# Quelle: Repo scripts/workstation/kai_vault_legacy_encrypt.ps1 (MindBlow 2.0, W0-8). Hier NICHT editieren --
# im Repo aendern, dann: pwsh -File scripts/workstation/install_workstation.ps1 -Apply
<#
.SYNOPSIS
    Einmalig (MindBlow 2.0, W0-9, 25.09.2026): Altbestand auf der Platte "KAI Backup"
    verschluesselt nach <Vault>\legacy\ ueberfuehren und pruefen.

.BESCHREIBUNG
    Der Altbestand (KAI-Backup-20260714, KAI-Backup-20260715, KAI-MIRROR) enthaelt
    Klartext-Geheimnisse: telegram_channel.session, rb-essentials (LND channel.db,
    wallet.db, Macaroons), Pi-Laufzeit-Tars mit dev.db. Seit dem Operator-Entscheid
    25.09. ist die Platte die Offsite-Kopie und liegt ausser Haus -- Klartext darf
    dort nicht bleiben.

    Dieses Skript LOESCHT NICHTS. Es schreibt je Altordner ein Archiv
    legacy\<ordner>.tar.gz.enc (KAI_BACKUP_PASSPHRASE, openssl wie kai_vault.ps1),
    prueft es (entschluesseln + listen, Dateizahl = Quelle) und schreibt
    legacy\LEGACY_MANIFEST.json. Das Loeschen des Klartexts ist ein getrennter
    Schritt mit Operator-Freigabe (-DeletePlaintext, nur fuer Ordner mit PASS).

    rb-essentials ist ein DANGER-Archiv: ein Restore dieser channel.db (Stand
    27.05.) gegen den laufenden Node waere ein Penalty-/Force-Close-Risiko. Es wird
    nur als historisches Material mitverschluesselt und in der README markiert.
#>
[CmdletBinding()]
param(
    [string]$TargetLabel = 'KAI Backup',
    [string[]]$Folders = @('KAI-Backup-20260714', 'KAI-Backup-20260715', 'KAI-MIRROR'),
    [switch]$DeletePlaintext
)
$ErrorActionPreference = 'Stop'
$PI = 'ubuntu@192.168.178.23'
$TarExe = Join-Path $env:SystemRoot 'System32\tar.exe'
$Ossl = 'C:\Program Files\Git\usr\bin\openssl.exe'
$GzipExe = 'C:\Program Files\Git\usr\bin\gzip.exe'
$vol = @(Get-Volume | Where-Object { $_.FileSystemLabel -eq $TargetLabel -and $_.DriveLetter })
if ($vol.Count -ne 1) { throw "Platte '$TargetLabel' nicht eindeutig angeschlossen" }
$drive = "$($vol[0].DriveLetter):\"
$vault = Join-Path $drive 'KAI-VAULT'
if (-not (Test-Path (Join-Path $vault '.kai-vault-id'))) { throw 'Vault nicht initialisiert (kai_vault.ps1 -Init)' }
$legacy = Join-Path $vault 'legacy'
New-Item -ItemType Directory -Force -Path $legacy | Out-Null
$manPath = Join-Path $legacy 'LEGACY_MANIFEST.json'
$man = if (Test-Path $manPath) { Get-Content $manPath -Raw | ConvertFrom-Json -AsHashtable } else { @{} }
function Q([string]$p) { '"' + $p + '"' }
function Invoke-Cmd([string]$Line) {
    $f = Join-Path $env:TEMP ('kai_legacy_{0}.cmd' -f [guid]::NewGuid().ToString('N'))
    try { [IO.File]::WriteAllText($f, "@echo off`r`n$Line`r`nexit /b %ERRORLEVEL%`r`n", [Text.Encoding]::ASCII); & cmd.exe /d /c $f | Out-Null; $LASTEXITCODE }
    finally { Remove-Item $f -Force -EA SilentlyContinue }
}

if ($DeletePlaintext) {
    foreach ($name in $Folders) {
        $e = $man[$name]
        $src = Join-Path $drive $name
        if (-not (Test-Path -LiteralPath $src)) { Write-Host "$name : Klartext bereits entfernt"; continue }
        if (-not $e -or $e.verdict -ne 'PASS' -or $e['gzip_crc'] -ne 'ok') { Write-Host "$name : KEIN PASS inkl. gzip-CRC im Manifest -- Klartext bleibt (erst ohne -DeletePlaintext nachpruefen)" -ForegroundColor Yellow; continue }
        $enc = Join-Path $legacy "$name.tar.gz.enc"
        if (-not (Test-Path -LiteralPath $enc) -or (Get-FileHash $enc -Algorithm SHA256).Hash.ToLower() -ne $e.sha256) { Write-Host "$name : Archiv fehlt oder sha weicht ab -- Klartext bleibt" -ForegroundColor Red; continue }
        Get-ChildItem -LiteralPath $src -Recurse -Force -File | ForEach-Object { $_.IsReadOnly = $false }
        # rd /s /q statt Remove-Item: Remove-Item brach auf exFAT an tiefen .git-Baeumen
        # mit "Verzeichnis ist nicht leer" ab (25.09.). Zwei Versuche, danach Existenzpruefung.
        foreach ($try in 1, 2) {
            if (-not (Test-Path -LiteralPath $src)) { break }
            $null = Invoke-Cmd "rd /s /q $(Q $src)"
            Start-Sleep -Seconds 2
        }
        if (Test-Path -LiteralPath $src) { Write-Host "$name : Loeschen unvollstaendig -- Rest bleibt, bitte erneut aufrufen" -ForegroundColor Red; continue }
        $man[$name]['plaintext_deleted_utc'] = (Get-Date).ToUniversalTime().ToString('o')
        Write-Host "$name : Klartext entfernt (verschluesselte Kopie geprueft: $enc)" -ForegroundColor Green
    }
    $man | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manPath -Encoding utf8
    exit 0
}

$raw = & ssh -n -o BatchMode=yes -o ConnectTimeout=20 $PI "grep -m1 '^KAI_BACKUP_PASSPHRASE=' /home/ubuntu/ai_analyst_trading_bot/.env" 2>$null | Select-Object -First 1
$p = ($raw -replace '^[^=]*=', '').Trim().Trim([char]39).Trim([char]34); $raw = $null
if ($p.Length -lt 16) { throw 'KAI_BACKUP_PASSPHRASE nicht lesbar' }
$env:KAI_LEGACY_PASS = $p; $p = $null
try {
    foreach ($name in $Folders) {
        $src = Join-Path $drive $name
        if (-not (Test-Path -LiteralPath $src)) { Write-Host "$name : Quelle fehlt"; continue }
        if ($man[$name] -and $man[$name].verdict -eq 'PASS') {
            if ($man[$name]['gzip_crc'] -eq 'ok') { Write-Host "$name : bereits verschluesselt und geprueft (inkl. gzip-CRC)"; continue }
            # Nachpruefung aelterer PASS-Eintraege: bsdtar erkennt gekippte Bytes nicht (25.09.), gzip -t schon.
            $encOld = Join-Path $legacy "$name.tar.gz.enc"
            $rcCrc = Invoke-Cmd "$(Q $Ossl) enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:KAI_LEGACY_PASS -in $(Q $encOld) 2>nul | $(Q $GzipExe) -t 2>nul"
            $shaOk = (Get-FileHash -LiteralPath $encOld -Algorithm SHA256).Hash.ToLower() -eq $man[$name].sha256
            if ($rcCrc -eq 0 -and $shaOk) { $man[$name]['gzip_crc'] = 'ok' } else { $man[$name]['gzip_crc'] = "FEHLER($rcCrc)"; $man[$name].verdict = 'FAIL' }
            Write-Host "$name : Nachpruefung gzip-CRC -> $($man[$name]['gzip_crc']), sha=$shaOk"
            $man | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manPath -Encoding utf8
            continue
        }
        $enc = Join-Path $legacy "$name.tar.gz.enc"
        $files = @(Get-ChildItem -LiteralPath $src -Recurse -Force -File -EA SilentlyContinue)
        $srcBytes = ($files | Measure-Object Length -Sum).Sum
        Write-Host ("{0}: {1} Dateien, {2:N2} GB -> {3}" -f $name, $files.Count, ($srcBytes / 1GB), $enc)
        $e1 = Join-Path $env:TEMP "kai_legacy_$name.tar.err"; $e2 = Join-Path $env:TEMP "kai_legacy_$name.ossl.err"
        # "D:\." statt "D:\": ein abschliessender Backslash vor dem Anfuehrungszeichen
        # entwertet es (C-Runtime-Parsing) -- tar sah dann keine Quelle (Lauf 1, 25.09.).
        $rc = Invoke-Cmd "$(Q $TarExe) -czf - -C $(Q ($drive + '.')) $(Q $name) 2>$(Q $e1) | $(Q $Ossl) enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -pass env:KAI_LEGACY_PASS -out $(Q $enc) 2>$(Q $e2)"
        $err = @(Get-Content $e1, $e2 -EA SilentlyContinue | Where-Object { $_.Trim() }); Remove-Item $e1, $e2 -Force -EA SilentlyContinue
        # Probe: entschluesseln + listen; Dateizahl (ohne Verzeichnis-Eintraege) = Quelle.
        $list = Join-Path $env:TEMP "kai_legacy_$name.list"; $e3 = Join-Path $env:TEMP "kai_legacy_$name.list.err"
        $rc2 = Invoke-Cmd "$(Q $Ossl) enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:KAI_LEGACY_PASS -in $(Q $enc) 2>nul | $(Q $TarExe) -tzf - >$(Q $list) 2>$(Q $e3)"
        $listed = @(Get-Content $list -EA SilentlyContinue | Where-Object { $_ -and -not $_.EndsWith('/') }).Count
        $err3 = @(Get-Content $e3 -EA SilentlyContinue | Where-Object { $_.Trim() }); Remove-Item $list, $e3 -Force -EA SilentlyContinue
        $rcCrc = Invoke-Cmd "$(Q $Ossl) enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:KAI_LEGACY_PASS -in $(Q $enc) 2>nul | $(Q $GzipExe) -t 2>nul"
        $ok = ($rc -eq 0 -and $rc2 -eq 0 -and $rcCrc -eq 0 -and $err.Count -eq 0 -and $err3.Count -eq 0 -and $listed -eq $files.Count)
        $sha = (Get-FileHash -LiteralPath $enc -Algorithm SHA256).Hash.ToLower()
        $man[$name] = [ordered]@{
            archive = "legacy\$name.tar.gz.enc"; sha256 = $sha; bytes = (Get-Item $enc).Length
            source_files = $files.Count; listed_files = $listed; source_bytes = $srcBytes
            verdict = $(if ($ok) { 'PASS' } else { 'FAIL' }); ts_utc = (Get-Date).ToUniversalTime().ToString('o')
            gzip_crc = $(if ($rcCrc -eq 0) { 'ok' } else { "FEHLER($rcCrc)" })
            errors = @($err + $err3 | Select-Object -First 5)
            note = $(if ($name -like 'KAI-Backup-*') { 'enthaelt 06-raspiblitz-migration\rb-essentials (LND channel.db/wallet.db 27.05.): DANGER -- nie gegen den laufenden Node zurueckspielen' } else { '' })
        }
        Write-Host ("  -> {0}  gelistet {1}/{2} Dateien  {3:N2} GB" -f $man[$name].verdict, $listed, $files.Count, ((Get-Item $enc).Length / 1GB)) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
        $man | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manPath -Encoding utf8
    }
} finally { $env:KAI_LEGACY_PASS = $null }
@"
# Legacy (verschluesselt) -- KAI-Vault

Altbestand der Platte vor dem Vault (Stand bis 27.08.2026), je Ordner ein Archiv, verschluesselt mit
KAI_BACKUP_PASSPHRASE (openssl enc -aes-256-cbc -pbkdf2 -iter 200000). Pruefsummen und Probe: LEGACY_MANIFEST.json.

**DANGER:** Die KAI-Backup-2026071x-Archive enthalten ``06-raspiblitz-migration\...\rb-essentials-20260527T120159Z.tar.gz``
(LND channel.db/wallet.db vom 27.05.). NIE gegen den laufenden Node zurueckspielen -- Penalty-/Force-Close-Risiko.
Kanal-Wiederherstellung ausschliesslich ueber channel.backup (SCB) + Seed.
"@ | Set-Content -LiteralPath (Join-Path $legacy 'README.md') -Encoding utf8
