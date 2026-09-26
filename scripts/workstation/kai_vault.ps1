# Quelle: Repo scripts/workstation/kai_vault.ps1 (MindBlow 2.0, W0-8). Hier NICHT editieren --
# im Repo aendern, dann: pwsh -File scripts/workstation/install_workstation.ps1 -Apply
<#
.SYNOPSIS
    KAI-Vault: verifizierte, generationierte und verschluesselte Offsite-Kopie
    von Pi, Lightning-Material und Laptop auf der Platte "KAI Backup" (D:).

.BESCHREIBUNG
    MindBlow 2.0, Welle 0 (Plan 25.09.2026). Nachfolger von kai_mirror_to_disk.ps1
    und make_d_backup.ps1. Warum ein Nachfolger statt einer Reparatur: Der alte
    Spiegel lief mit /MIR ohne Generationen, verglich den Pi-Hash mit nichts,
    "pruefte" die Entschluesselung mit `echo PASS_PRESENT`, endete bei
    Teilfehlern mit Exit 0 und lief seit dem 27.08. nie wieder, weil ihn kein
    Task startete. Alles Laptop-seitig Verschluesselte hing an DPAPI und waere
    nach einem Laptop-Verlust wertlos gewesen.

    Operator-Entscheide 25.09.:
      * Verschluesselung mit der vorhandenen KAI_BACKUP_PASSPHRASE
        (openssl aes-256-cbc, PBKDF2, 200k -- dasselbe Format wie die
        Pi-Tagesarchive). Kein BitLocker, keine Neuformatierung.
      * Lauf beim Anstecken der Platte (Task, Ereignis), hoechstens einmal je 20 h.
      * Die Platte ist die Offsite-Kopie: nach dem Lauf abziehen, getrennt verwahren.

    Eigenschaften:
      * ZIEL PER LABEL + IDENTITAET. Label 'KAI Backup' und <Vault>\.kai-vault-id
        (UUID + Volume-Id). Eine fremde Platte mit gleichem Label wird abgelehnt.
      * GENERATIONEN statt Spiegel. gen\<UTC-Stempel>\, nach bestandener Probe
        schreibgeschuetzt; Aufbewahrung 7 Tage / 4 Wochen / 12 Monate, mindestens
        3 verifizierte Generationen; geloescht wird nur nach einer frisch
        verifizierten Generation.
      * KEIN KLARTEXT-SECRET AUF DER PLATTE. Der Pi-Zustand wird AUF DER PI gepackt
        und verschluesselt (SQLite ueber die Backup-API konsistent); Laptop-Quellen
        laufen als tar | openssl direkt auf die Platte. Unverschluesselt bleiben nur
        die KeePass-DB (eigenverschluesselt), channel.backup (von LND
        verschluesselt) und Metadaten (Manifest, README, Status, Logs).
      * SCHLUESSELTRENNUNG. Authentisierungsmaterial (Pi ~/kai-secrets mit
        HOTP-Seed und Macaroons, Laptop .ssh, Codex-/Claude-Anmeldung) nur mit
        LN_SECRET_BACKUP_KEY. Fehlt der Schluessel: PARTIAL, KEIN Rueckfall auf die
        Artefakt-Passphrase (Operator-Direktive 27.08.).
      * BEWEIS STATT BEHAUPTUNG. Pi-sha256 = D:-sha256 je Archiv; jede neue
        Generation wird sofort entschluesselt und gelistet (Eintragszahl = Quelle),
        dev.db per PRAGMA integrity_check, das Zahlungsjournal per verify_chain,
        das Git-Bundle per `git bundle verify` geprueft. Erst dann VERIFIED.json.
      * EHRLICHE EXIT-CODES. 0 = OK, 2 = PARTIAL (Quelle oder Schluessel fehlt),
        1 = FATAL (Ziel fehlt, Verschluesselung oder Pruefung gescheitert). Jeder
        Lauf schreibt ein Log auf die Platte UND nach C:\Users\sasch\KAI-mirror\vault-logs.

    Geheimnisse erscheinen nie in Log oder Kommandozeile: Passphrasen stehen nur
    in Prozess-Umgebungsvariablen (openssl -pass env:) und werden am Ende geleert.

.PARAMETER Init
    Vault auf der Platte mit Label -TargetLabel neu anlegen (.kai-vault-id).
.PARAMETER Force
    Die 20-h-Sperre ignorieren.
.PARAMETER ProbeOnly
    Nur die neueste (oder -Generation) Generation erneut pruefen.
.PARAMETER StoreLnKey
    LN_SECRET_BACKUP_KEY einmalig interaktiv eingeben und DPAPI-geschuetzt unter
    %USERPROFILE%\.kai\vault ablegen (Arbeitskopie; das Original bleibt in KeePass).
.PARAMETER TestTarget
    Statt der Platte ein Verzeichnis verwenden (Test). Keine Label-Pruefung.

.BEISPIEL
    pwsh -File kai_vault.ps1 -Init            # einmalig, Platte angesteckt
    pwsh -File kai_vault.ps1                  # normaler Lauf (Task beim Anstecken)
    pwsh -File kai_vault.ps1 -ProbeOnly       # neueste Generation nachpruefen
    pwsh -File kai_vault.ps1 -StoreLnKey      # LN-Schluessel hinterlegen (interaktiv)
#>
[CmdletBinding()]
param(
    [string]$TargetLabel = 'KAI Backup',
    [string]$VaultDirName = 'KAI-VAULT',
    [string]$TestTarget,
    [switch]$Init,
    [switch]$InitOnly,
    [switch]$Force,
    [switch]$SkipPi,
    [switch]$SkipLaptop,
    [switch]$SkipStatic,
    [switch]$ProbeOnly,
    [string]$Generation,
    [switch]$StoreLnKey,
    [switch]$NoToast,
    # Task-Modus: Platte fehlt -> kein FATAL, sondern still beenden; ist die
    # letzte verifizierte Generation aelter als -RemindAfterDays, erinnert ein Toast.
    [switch]$Quiet,
    [int]$WaitForDiskSeconds = 0,
    [int]$RemindAfterDays = 7,
    [int]$MinHoursBetweenRuns = 20,
    [int]$MinFreeGB = 20
)

$ErrorActionPreference = 'Stop'
$VaultVersion = 'kai_vault/1.0 (2026-09-25)'

# ── Konstanten ──────────────────────────────────────────────────────────────
$PI         = 'ubuntu@192.168.178.23'
$PiHost     = '192.168.178.23'
$PiRepo     = '/home/ubuntu/ai_analyst_trading_bot'
$UserHome   = 'C:\Users\sasch'
$MainRepo   = 'C:\Users\sasch\.local\bin\ai_analyst_trading_bot'
$LocalLogs  = 'C:\Users\sasch\KAI-mirror\vault-logs'
$KeyDir     = Join-Path $env:USERPROFILE '.kai\vault'
$LnKeyFile  = Join-Path $KeyDir 'ln_secret_backup_key.dpapi'
$TaskExport = Join-Path $KeyDir 'tasks-export'
$EnvBackups = 'C:\Users\sasch\KAI-mirror\env-backups'
$ScbSource  = 'C:\Users\sasch\channel.backup'
$StaticImg  = 'C:\Users\sasch\raspiblitz-migration\image'
$TarExe     = Join-Path $env:SystemRoot 'System32\tar.exe'
$Ossl       = (Get-Command openssl -EA SilentlyContinue).Source
if (-not $Ossl -and (Test-Path 'C:\Program Files\Git\usr\bin\openssl.exe')) { $Ossl = 'C:\Program Files\Git\usr\bin\openssl.exe' }
$GzipExe    = Join-Path ${env:ProgramFiles} 'Git\usr\bin\gzip.exe'
$GitExe     = (Get-Command git -EA SilentlyContinue).Source
$PyExe      = (Get-Command python -EA SilentlyContinue).Source
$SshExe     = (Get-Command ssh -EA SilentlyContinue).Source
$ScpExe     = (Get-Command scp -EA SilentlyContinue).Source
$Stamp      = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH-mm-ssZ')
$DesktopProjects = @('SENTR', 'Watchdog', 'Satoshi', 'KAI-vdb5-ui', 'Architect', 'KAI Text', 'DALI',
    'Einstein', 'Neo - Kopie', 'KAI-Finder', 'Projektzeitleiste timeline', 'Neo', 'Xqu')
$KeePassDirs = @('C:\Users\sasch\KeePass', 'C:\Users\sasch\Documents', 'C:\Users\sasch\Downloads',
    'C:\Users\sasch\OneDrive\Dokumente')
$OpensslEnc = 'enc -aes-256-cbc -salt -pbkdf2 -iter 200000'
$OpensslDec = 'enc -d -aes-256-cbc -pbkdf2 -iter 200000'
$MaxUntrackedBytes = 50MB

# Testlaeufe getrennt halten: ihre Quittungen duerfen keine echte Erinnerung unterdruecken.
if ($TestTarget) { $LocalLogs = Join-Path $LocalLogs 'test' }
New-Item -ItemType Directory -Force -Path $LocalLogs, $KeyDir | Out-Null
$Log = Join-Path $LocalLogs "vault_$Stamp.log"
$Results = [System.Collections.Generic.List[object]]::new()

function Log([string]$m) {
    $line = '{0}  {1}' -f (Get-Date).ToString('s'), $m
    Add-Content -LiteralPath $Log -Value $line -Encoding utf8
    Write-Host $line
}
function Q([string]$p) { '"' + $p + '"' }
function Add-Result {
    param([string]$Name, [ValidateSet('OK', 'WARN', 'PARTIAL', 'FAIL', 'SKIPPED')][string]$Status,
        [string]$Detail = '', [string]$File = '', [long]$Bytes = 0, [string]$Sha = '', [hashtable]$Extra = @{})
    $r = [ordered]@{ name = $Name; status = $Status; detail = $Detail; file = $File; bytes = $Bytes; sha256 = $Sha }
    foreach ($k in $Extra.Keys) { $r[$k] = $Extra[$k] }
    $Results.Add([pscustomobject]$r)
    Log ('[{0}] {1} -- {2}' -f $Status, $Name, $Detail)
}
function Get-Sha256([string]$path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower() }
function New-TempPath([string]$suffix) { Join-Path $env:TEMP ('kai_vault_{0}_{1}' -f [guid]::NewGuid().ToString('N'), $suffix) }

# cmd.exe-Pipeline ueber eine temporaere .cmd-Datei: byte-sicher fuer
# tar | openssl und ssh > Datei, unabhaengig von der PowerShell-Version. Die
# Datei enthaelt nur Pfade und Befehle; Schluessel kommen ueber env:.
function Invoke-Cmd([string]$Line) {
    $f = New-TempPath 'run.cmd'
    try {
        [IO.File]::WriteAllText($f, "@echo off`r`n$Line`r`nexit /b %ERRORLEVEL%`r`n", [Text.Encoding]::ASCII)
        & cmd.exe /d /c $f | Out-Null
        return $LASTEXITCODE
    } finally { Remove-Item -LiteralPath $f -Force -EA SilentlyContinue }
}
function Read-ErrLines([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return @() }
    $l = @(Get-Content -LiteralPath $path -EA SilentlyContinue | Where-Object { $_.Trim() })
    Remove-Item -LiteralPath $path -Force -EA SilentlyContinue
    return $l
}

# ── Schluessel ──────────────────────────────────────────────────────────────
Add-Type -AssemblyName System.Security.Cryptography.ProtectedData -EA SilentlyContinue

function Get-BackupPassphrase {
    # 1) Pi-.env -- Quelle der Wahrheit fuer die Artefakt-Passphrase.
    try {
        $raw = & $SshExe -n -o BatchMode=yes -o ConnectTimeout=20 $PI "grep -m1 '^KAI_BACKUP_PASSPHRASE=' $PiRepo/.env" 2>$null | Select-Object -First 1
        if ($raw) {
            $p = ($raw -replace '^[^=]*=', '').Trim().Trim([char]39).Trim([char]34)
            if ($p.Length -ge 16) { return @{ value = $p; source = 'pi_env' } }
        }
    } catch { }
    # 2) Neueste DPAPI-.env-Sicherung dieses Laptops (Pi nicht erreichbar).
    try {
        $stem = (Get-ChildItem $EnvBackups -Filter 'env_backup_*.aes' -EA Stop | Sort-Object Name | Select-Object -Last 1).Name -replace '\.aes$', ''
        $kiv = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes("$EnvBackups\$stem.key.dpapi"), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $aes = [Security.Cryptography.Aes]::Create(); $aes.Key = $kiv[0..31]; $aes.IV = $kiv[32..47]
        $ct = [IO.File]::ReadAllBytes("$EnvBackups\$stem.aes")
        $pt = $aes.CreateDecryptor().TransformFinalBlock($ct, 0, $ct.Length)
        [Array]::Clear($kiv, 0, $kiv.Length)
        $txt = [Text.Encoding]::UTF8.GetString($pt); [Array]::Clear($pt, 0, $pt.Length)
        $m = [regex]::Match($txt, '(?m)^KAI_BACKUP_PASSPHRASE=(.*)$')
        $txt = $null
        if ($m.Success) {
            $p = $m.Groups[1].Value.Trim().Trim([char]39).Trim([char]34)
            if ($p.Length -ge 16) { return @{ value = $p; source = "env_backup_dpapi:$stem" } }
        }
    } catch { }
    return $null
}

function Get-LnKey {
    foreach ($scope in 'Process', 'User') {
        $v = [Environment]::GetEnvironmentVariable('LN_SECRET_BACKUP_KEY', $scope)
        if ($v -and $v.Length -ge 32) { return @{ value = $v; source = "env:$scope" } }
    }
    if (Test-Path -LiteralPath $LnKeyFile) {
        try {
            $b = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($LnKeyFile), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
            $v = [Text.Encoding]::UTF8.GetString($b); [Array]::Clear($b, 0, $b.Length)
            if ($v.Length -ge 32) { return @{ value = $v; source = 'dpapi_file' } }
        } catch { }
    }
    return $null
}

if ($StoreLnKey) {
    $a = Read-Host -AsSecureString 'LN_SECRET_BACKUP_KEY (aus KeePass einfuegen)'
    $b = Read-Host -AsSecureString 'Zur Kontrolle erneut'
    $pa = [Runtime.InteropServices.Marshal]::PtrToStringUni([Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($a))
    $pb = [Runtime.InteropServices.Marshal]::PtrToStringUni([Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($b))
    if ($pa -ne $pb) { Write-Host 'Eingaben unterschiedlich -- nichts gespeichert.' -ForegroundColor Red; exit 1 }
    if ($pa.Length -lt 32) { Write-Host 'Schluessel kuerzer als 32 Zeichen -- nichts gespeichert.' -ForegroundColor Red; exit 1 }
    $prot = [Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($pa), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    [IO.File]::WriteAllBytes($LnKeyFile, $prot)
    $pa = $null; $pb = $null
    Write-Host "LN_SECRET_BACKUP_KEY DPAPI-geschuetzt abgelegt: $LnKeyFile (Original bleibt in KeePass)." -ForegroundColor Green
    exit 0
}

# ── Toast (Windows PowerShell 5.1 kennt die WinRT-Projektion) ──────────────
function Show-Toast([string]$title, [string]$text) {
    if ($NoToast) { return }
    $t = [Security.SecurityElement]::Escape($title) -replace "'", "''"
    $x = [Security.SecurityElement]::Escape($text) -replace "'", "''"
    $ps = "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; " +
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null; " +
    "`$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; " +
    "`$xml.LoadXml('<toast><visual><binding template=""ToastGeneric""><text>$t</text><text>$x</text></binding></visual></toast>'); " +
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show([Windows.UI.Notifications.ToastNotification]::new(`$xml))"
    try { & powershell.exe -NoProfile -NonInteractive -Command $ps 2>$null | Out-Null } catch { }
}

# ── Ziel finden ────────────────────────────────────────────────────────────
function Resolve-Vault {
    if ($TestTarget) {
        New-Item -ItemType Directory -Force -Path $TestTarget | Out-Null
        $root = (Resolve-Path $TestTarget).Path
        $idf = Join-Path $root '.kai-vault-id'
        if (-not (Test-Path $idf)) {
            [ordered]@{ vault_id = [guid]::NewGuid().ToString(); created_utc = (Get-Date).ToUniversalTime().ToString('o'); label = 'TEST'; volume_unique_id = '' } |
                ConvertTo-Json | Set-Content -LiteralPath $idf -Encoding utf8
        }
        return @{ root = $root; id = (Get-Content $idf -Raw | ConvertFrom-Json); volume = $null; freeGB = 999 }
    }
    # Nach dem Anstecken braucht Windows einige Sekunden bis zum Laufwerksbuchstaben.
    $deadline = (Get-Date).AddSeconds($WaitForDiskSeconds)
    do {
        $vols = @(Get-Volume | Where-Object { $_.FileSystemLabel -eq $TargetLabel -and $_.DriveLetter })
        if ($vols.Count -gt 0 -or (Get-Date) -ge $deadline) { break }
        Start-Sleep -Seconds 5
    } while ($true)
    if ($vols.Count -eq 0) { return @{ error = "Platte mit Label '$TargetLabel' nicht angeschlossen"; absent = $true } }
    if ($vols.Count -gt 1) { return @{ error = "Mehrere Volumes mit Label '$TargetLabel'" } }
    $vol = $vols[0]
    $root = '{0}:\{1}' -f $vol.DriveLetter, $VaultDirName
    $idf = Join-Path $root '.kai-vault-id'
    if (-not (Test-Path -LiteralPath $idf)) {
        if (-not $Init) { return @{ error = "Vault auf '$TargetLabel' nicht initialisiert ($idf fehlt) -- einmalig mit -Init anlegen" } }
        New-Item -ItemType Directory -Force -Path $root | Out-Null
        [ordered]@{ vault_id = [guid]::NewGuid().ToString(); created_utc = (Get-Date).ToUniversalTime().ToString('o')
            label = $TargetLabel; volume_unique_id = $vol.UniqueId; created_by = $VaultVersion } |
            ConvertTo-Json | Set-Content -LiteralPath $idf -Encoding utf8
        Log "Vault initialisiert: $root"
    }
    $id = Get-Content -LiteralPath $idf -Raw | ConvertFrom-Json
    if ($id.volume_unique_id -and $id.volume_unique_id -ne $vol.UniqueId) {
        return @{ error = 'Volume-Id passt nicht zur Vault-Identitaet (andere Platte mit gleichem Label?) -- abgelehnt' }
    }
    return @{ root = $root; id = $id; volume = $vol; freeGB = [math]::Round($vol.SizeRemaining / 1GB, 1) }
}

# ── GFS-Aufbewahrung (reine Funktion) ──────────────────────────────────────
function ConvertFrom-GenName([string]$n) {
    [datetime]::ParseExact($n, "yyyy-MM-dd'T'HH-mm-ss'Z'", [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal)
}
function Test-GenName([string]$n) { $n -match '^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z$' }
function Get-GfsKeep {
    param([string[]]$Verified, [int]$Daily = 7, [int]$Weekly = 4, [int]$Monthly = 12, [int]$MinKeep = 3)
    $items = @($Verified | Where-Object { Test-GenName $_ } |
            ForEach-Object { [pscustomobject]@{ name = $_; t = (ConvertFrom-GenName $_) } } | Sort-Object t -Descending)
    $keep = [System.Collections.Generic.HashSet[string]]::new()
    $rules = @(
        @{ n = $Daily; k = { param($t) $t.ToString('yyyy-MM-dd') } },
        @{ n = $Weekly; k = { param($t) '{0}-W{1:00}' -f [Globalization.ISOWeek]::GetYear($t), [Globalization.ISOWeek]::GetWeekOfYear($t) } },
        @{ n = $Monthly; k = { param($t) $t.ToString('yyyy-MM') } }
    )
    foreach ($rule in $rules) {
        $seen = [System.Collections.Generic.HashSet[string]]::new()
        foreach ($i in $items) {
            if ($seen.Count -ge $rule.n) { break }
            if ($seen.Add((& $rule.k $i.t))) { [void]$keep.Add($i.name) }
        }
    }
    $items | Select-Object -First $MinKeep | ForEach-Object { [void]$keep.Add($_.name) }
    return @($keep)
}

# ── Probe einer Generation ─────────────────────────────────────────────────
function Test-EncList([string]$encFile, [string]$passVar) {
    $list = New-TempPath 'list.txt'; $e1 = New-TempPath 'e1.txt'; $e2 = New-TempPath 'e2.txt'
    $rc = Invoke-Cmd "$(Q $Ossl) $OpensslDec -pass env:$passVar -in $(Q $encFile) 2>$(Q $e1) | $(Q $TarExe) -tzf - >$(Q $list) 2>$(Q $e2)"
    $err = @(Read-ErrLines $e1) + @(Read-ErrLines $e2)
    $n = 0
    if (Test-Path -LiteralPath $list) { $n = @(Get-Content -LiteralPath $list).Count; Remove-Item -LiteralPath $list -Force -EA SilentlyContinue }
    # CRC32 des gzip-Stroms: bsdtar meldet ein gekipptes Byte weder beim Listen noch
    # beim Entpacken (gemessen 25.09.), gzip -t schon. Damit erkennt die Probe
    # Datenfehler auch ohne das Manifest (CBC ohne MAC, R10).
    $e3 = New-TempPath 'e3.txt'
    $crc = Invoke-Cmd "$(Q $Ossl) $OpensslDec -pass env:$passVar -in $(Q $encFile) 2>nul | $(Q $GzipExe) -t 2>$(Q $e3)"
    $err += @(Read-ErrLines $e3)
    return @{ ok = ($rc -eq 0 -and $crc -eq 0 -and $n -gt 0 -and $err.Count -eq 0); members = $n; rc = $rc; crc = $crc; err = (($err | Select-Object -First 3) -join ' | ') }
}

function Invoke-Probe([string]$genDir) {
    $probe = [System.Collections.Generic.List[object]]::new()
    $add = {
        param($c, $ok, $d)
        $probe.Add([pscustomobject]@{ check = $c; pass = [bool]$ok; detail = "$d" })
        Log ('  probe [{0}] {1} -- {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $c, $d)
    }
    $manPath = Join-Path $genDir 'MANIFEST.json'
    if (-not (Test-Path -LiteralPath $manPath)) { & $add 'manifest.present' $false 'MANIFEST.json fehlt'; return , $probe }
    $man = Get-Content -LiteralPath $manPath -Raw | ConvertFrom-Json
    foreach ($s in $man.sources) {
        if (-not $s.file) { continue }
        $f = Join-Path $genDir $s.file
        if (-not (Test-Path -LiteralPath $f)) { & $add "$($s.name).present" $false "fehlt: $($s.file)"; continue }
        $sha = Get-Sha256 $f
        & $add "$($s.name).sha256" ($sha -eq $s.sha256) ('{0}.. gegen Manifest' -f $sha.Substring(0, 16))
        $passVar = switch ($s.key) { 'ln' { 'KAI_VAULT_LNKEY' } 'backup' { 'KAI_VAULT_PASS' } default { $null } }
        if (-not $passVar) { continue }
        if (-not [Environment]::GetEnvironmentVariable($passVar)) { & $add "$($s.name).decrypt" $false "Schluessel '$($s.key)' nicht verfuegbar"; continue }
        if ($s.kind -eq 'bundle') {
            $tmpB = New-TempPath 'kai.bundle'; $e1 = New-TempPath 'e.txt'
            $rc = Invoke-Cmd "$(Q $Ossl) $OpensslDec -pass env:$passVar -in $(Q $f) -out $(Q $tmpB) 2>$(Q $e1)"
            $null = Read-ErrLines $e1
            if ($rc -ne 0) { & $add "$($s.name).decrypt" $false "openssl rc=$rc"; Remove-Item $tmpB -Force -EA SilentlyContinue; continue }
            $vOut = & $GitExe -C $MainRepo bundle verify $tmpB 2>&1 | Out-String
            $vRc = $LASTEXITCODE
            $heads = @(& $GitExe -C $MainRepo bundle list-heads $tmpB 2>$null).Count
            Remove-Item $tmpB -Force -EA SilentlyContinue
            & $add "$($s.name).bundle_verify" ($vRc -eq 0 -and $heads -gt 0 -and $heads -eq [int]$s.members_source) "heads=$heads quelle=$($s.members_source)"
            continue
        }
        $r = Test-EncList $f $passVar
        $okCount = $true; $detail = "eintraege=$($r.members) rc=$($r.rc) gzip_crc=$(if ($r.crc -eq 0) { 'ok' } else { "FEHLER($($r.crc))" }) $($r.err)"
        if ($s.PSObject.Properties['members_source'] -and "$($s.members_source)" -ne '') {
            $okCount = ([int]$s.members_source -eq $r.members); $detail += " quelle=$($s.members_source)"
        }
        & $add "$($s.name).decrypt_list" ($r.ok -and $okCount) $detail
        if ($s.name -eq 'pi_state' -and $r.ok) {
            # Tiefenprobe: dev.db integrity_check + Kette des Zahlungsjournals.
            $x = New-TempPath 'x'; New-Item -ItemType Directory -Force -Path $x | Out-Null
            try {
                $e1 = New-TempPath 'e1.txt'; $e2 = New-TempPath 'e2.txt'
                $rc = Invoke-Cmd "$(Q $Ossl) $OpensslDec -pass env:$passVar -in $(Q $f) 2>$(Q $e1) | $(Q $TarExe) -xzf - -C $(Q $x) ./data/dev.db ./artifacts/payments/payment_journal.jsonl 2>$(Q $e2)"
                $null = Read-ErrLines $e1; $null = Read-ErrLines $e2
                $db = Join-Path $x 'data\dev.db'; $pj = Join-Path $x 'artifacts\payments\payment_journal.jsonl'
                if (Test-Path -LiteralPath $db) {
                    $ic = & $PyExe -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()" $db 2>&1 | Select-Object -Last 1
                    & $add 'pi_state.dev_db_integrity' ("$ic" -eq 'ok') "integrity_check=$ic"
                } else { & $add 'pi_state.dev_db_integrity' $false "data/dev.db nicht im Archiv (rc=$rc)" }
                if (Test-Path -LiteralPath $pj) {
                    $code = 'import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from app.payments.journal import PaymentJournal; s = PaymentJournal(Path(sys.argv[2])).verify_chain(); print(("OK" if s.ok else "BROKEN"), s.records, s.reason)'
                    $vc = & $PyExe -c $code $MainRepo $pj 2>&1 | Select-Object -Last 1
                    & $add 'pi_state.payment_journal_chain' ("$vc" -match '^OK ') "$vc"
                } else { & $add 'pi_state.payment_journal_chain' $false 'payment_journal.jsonl nicht im Archiv' }
            } finally { Remove-Item -LiteralPath $x -Recurse -Force -EA SilentlyContinue }
        }
    }
    return , $probe
}

# Quittung auf der Pi (MindBlow 2.0, E3): die Health-Sonde dort meldet, wenn die
# neueste verifizierte Offsite-Generation zu alt ist -- das ist zugleich die
# Erinnerung, die Platte anzustecken. Nur Evidenz, kein Zustand: die Wahrheit
# bleibt die Generation auf der Platte (VERIFIED.json).
$PiReceiptDir = "$PiRepo/artifacts/backup"
$PiReceiptFile = "$PiReceiptDir/offpi_receipts.jsonl"
function Send-OffPiReceipt([string]$genDir, [string]$probeVerdict, [int]$checks) {
    if ($TestTarget) { return 'SKIPPED_TEST' }
    if ($probeVerdict -ne 'PASS') { return 'SKIPPED_PROBE_FAIL' }
    $genName = Split-Path $genDir -Leaf
    $man = Get-Content -LiteralPath (Join-Path $genDir 'MANIFEST.json') -Raw | ConvertFrom-Json
    $src = @{}; foreach ($s in $man.sources) { $src[$s.name] = $s }
    $pick = { param($n, $f) if ($src.ContainsKey($n) -and $src[$n].PSObject.Properties[$f]) { $src[$n].$f } else { $null } }
    $receipt = [ordered]@{
        schema             = 'offpi_receipt/v1'
        ts_utc             = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        location           = 'KAI-VAULT (Platte "KAI Backup", offline/offsite)'
        vault_id           = $man.vault_id
        generation         = $genName
        generation_ts_utc  = (ConvertFrom-GenName $genName).ToString('yyyy-MM-ddTHH:mm:ssZ')
        probe              = $probeVerdict
        probe_checks       = $checks
        partial_sources    = @($man.sources | Where-Object { $_.status -in 'PARTIAL', 'SKIPPED' } | ForEach-Object { $_.name })
        pi_state_sha256    = (& $pick 'pi_state' 'sha256')
        pi_backup_archive  = (& $pick 'pi_backup' 'source_path')
        pi_backup_sha256   = (& $pick 'pi_backup' 'sha256')
        tool               = $VaultVersion
    }
    $line = ($receipt | ConvertTo-Json -Compress -Depth 4)
    $tmp = New-TempPath 'receipt.jsonl'
    [IO.File]::WriteAllText($tmp, $line + "`n", (New-Object Text.UTF8Encoding($false)))
    # Schreiber auf der Pi: app.observability.offpi_receipts (Schema-Pruefung,
    # gesperrtes Anhaengen). Solange ein Release das Modul noch nicht auf die Pi
    # gebracht hat, faellt der Befehl auf direktes Anhaengen zurueck und sagt das.
    $remote = "cd $PiRepo && if .venv/bin/python -c 'import app.observability.offpi_receipts' 2>/dev/null; " +
              "then .venv/bin/python -m app.observability.offpi_receipts append; " +
              "else mkdir -p $PiReceiptDir && cat >> $PiReceiptFile && echo OFFPI_RECEIPT_FALLBACK; fi"
    $out = New-TempPath 'receipt.out'; $err = New-TempPath 'receipt.err'
    try {
        $rc = Invoke-Cmd "$(Q $SshExe) -o BatchMode=yes -o ConnectTimeout=20 $PI `"$remote`" <$(Q $tmp) >$(Q $out) 2>$(Q $err)"
        $said = @(Get-Content -LiteralPath $out -EA SilentlyContinue) + @(Read-ErrLines $err)
    } finally { Remove-Item -LiteralPath $tmp, $out -Force -EA SilentlyContinue }
    $said = ($said | Where-Object { $_ -match '^OFFPI_RECEIPT_' }) -join ' | '
    if ($rc -eq 0 -and $said -match 'OFFPI_RECEIPT_OK') { Log "Quittung auf der Pi (Schreiber-Modul): $genName, Probe $probeVerdict"; return 'SENT' }
    if ($rc -eq 0 -and $said -match 'OFFPI_RECEIPT_FALLBACK') { Log "Quittung auf der Pi (Rueckfall cat >>, Modul noch nicht deployt): $genName"; return 'SENT_FALLBACK' }
    Log "WARN: Quittung NICHT geschrieben (ssh rc=$rc; $said) -- die Pi-Sonde meldet sonst in 8 Tagen 'zu alt'"
    return "FAILED(rc=$rc)"
}

function Write-Status([hashtable]$h) {
    $h['ts_utc'] = (Get-Date).ToUniversalTime().ToString('o')
    $json = [pscustomobject]$h | ConvertTo-Json -Depth 6
    Set-Content -LiteralPath (Join-Path $LocalLogs 'vault_status.json') -Value $json -Encoding utf8
    if ($script:VaultRoot -and (Test-Path -LiteralPath $script:VaultRoot)) {
        Set-Content -LiteralPath (Join-Path $script:VaultRoot 'status\vault_status.json') -Value $json -Encoding utf8
    }
}

# ════════════════════════════════════════════════════════════════════════════
Log "=== $VaultVersion  stamp=$Stamp ==="
$v = Resolve-Vault
if ($v.error -and $v.absent -and $Quiet) {
    # Task-Modus ohne Platte: kein Fehler. Erinnerung, wenn die letzte
    # verifizierte Generation zu alt ist (Quelle: Quittungen auf C:).
    $rcpt = Join-Path $LocalLogs 'receipts.jsonl'
    $lastOk = $null
    if (Test-Path -LiteralPath $rcpt) {
        $lastOk = Get-Content -LiteralPath $rcpt | ForEach-Object { try { $_ | ConvertFrom-Json } catch { } } |
            Where-Object { $_.probe -eq 'PASS' } | ForEach-Object { [datetime]$_.ts_utc } | Sort-Object | Select-Object -Last 1
    }
    $days = if ($lastOk) { ((Get-Date).ToUniversalTime() - $lastOk.ToUniversalTime()).TotalDays } else { 999 }
    if ($days -ge $RemindAfterDays) {
        $txt = if ($lastOk) { 'Letzte verifizierte Sicherung vor {0:N0} Tagen.' -f $days } else { 'Noch keine verifizierte Sicherung.' }
        Show-Toast 'KAI-Vault: Platte anstecken' "$txt Platte 'KAI Backup' anstecken -- der Lauf startet automatisch."
        Log "Platte nicht angeschlossen; Erinnerung gesendet ($txt)"
    } else {
        Remove-Item -LiteralPath $Log -Force -EA SilentlyContinue
    }
    exit 0
}
if ($v.error) {
    Log "FATAL: $($v.error)"
    Write-Status @{ exit = 1; verdict = 'FATAL'; reason = $v.error }
    exit 1
}
$script:VaultRoot = $v.root
foreach ($d in 'gen', 'logs', 'status', 'static', 'legacy') { New-Item -ItemType Directory -Force -Path (Join-Path $VaultRoot $d) | Out-Null }
Log "Vault: $VaultRoot  id=$($v.id.vault_id)  frei=$($v.freeGB) GB"
$rootReadme = @"
# KAI-VAULT -- Offline-/Offsite-Sicherung von KAI

Diese Platte ist die Offsite-Kopie von KAI (Operator-Entscheid 25.09.2026): nach jedem Lauf abziehen und
raeumlich getrennt von Pi und Laptop verwahren. Erzeugt von ``$VaultVersion`` (C:\Users\sasch\KAI-mirror\scripts\kai_vault.ps1).

- ``gen\<UTC-Stempel>\`` -- je Lauf eine Generation. Nur Generationen mit ``VERIFIED.json`` sind geprueft
  (sha256, Entschluesseln + Listen, dev.db integrity_check, Zahlungsjournal-Kette, Git-Bundle). Jede Generation
  hat ein eigenes ``README-RESTORE.md`` und ``MANIFEST.json``.
- ``legacy\`` -- Altbestand bis 27.08.2026, verschluesselt (``LEGACY_MANIFEST.json``). DANGER: enthaelt eine alte
  LND channel.db (rb-essentials 27.05.) -- nie gegen einen laufenden Node zurueckspielen.
- ``static\`` -- einmalige Artefakte (RaspiBlitz-Image mit SHA256SUMS).
- ``status\``, ``logs\`` -- letzter Lauf, letzte Probe, Protokolle.

Schluessel liegen NICHT auf dieser Platte, sondern in KeePass (+ Notfallblatt):
``KAI_BACKUP_PASSPHRASE`` fuer fast alles, ``LN_SECRET_BACKUP_KEY`` nur fuer Authentisierungsmaterial.
Entschluesseln: ``openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in <datei>.enc -out <datei>``.
Kanal-Wiederherstellung (Lightning) nur ueber ``channel.backup`` (SCB) + Wallet-Seed (Papier).
"@
Set-Content -LiteralPath (Join-Path $VaultRoot 'README.md') -Value $rootReadme -Encoding utf8
if ($Init -and $InitOnly) { Log 'Nur Initialisierung (-InitOnly).'; exit 0 }
foreach ($t in @(@('openssl', $Ossl), @('tar', $TarExe), @('gzip', $GzipExe), @('git', $GitExe), @('python', $PyExe), @('ssh', $SshExe), @('scp', $ScpExe))) {
    if (-not $t[1] -or -not (Test-Path -LiteralPath $t[1])) { Log "FATAL: Werkzeug fehlt: $($t[0])"; Write-Status @{ exit = 1; verdict = 'FATAL'; reason = "tool $($t[0])" }; exit 1 }
}
if ($v.freeGB -lt $MinFreeGB) { Log "FATAL: nur $($v.freeGB) GB frei (< $MinFreeGB)"; Write-Status @{ exit = 1; verdict = 'FATAL'; reason = 'disk_full' }; exit 1 }

$genRoot = Join-Path $VaultRoot 'gen'
function Get-VerifiedGens {
    @(Get-ChildItem -LiteralPath $genRoot -Directory -EA SilentlyContinue |
            Where-Object { (Test-GenName $_.Name) -and (Test-Path -LiteralPath (Join-Path $_.FullName 'VERIFIED.json')) } | Sort-Object Name)
}

# ── Nur Probe ──────────────────────────────────────────────────────────────
if ($ProbeOnly) {
    $target = if ($Generation) { Join-Path $genRoot $Generation } else { (Get-ChildItem -LiteralPath $genRoot -Directory | Where-Object { Test-GenName $_.Name } | Sort-Object Name | Select-Object -Last 1).FullName }
    if (-not $target -or -not (Test-Path -LiteralPath $target)) { Log 'FATAL: keine Generation gefunden'; exit 1 }
    $bp = Get-BackupPassphrase; $lk = Get-LnKey
    try {
        if ($bp) { $env:KAI_VAULT_PASS = $bp.value }
        if ($lk) { $env:KAI_VAULT_LNKEY = $lk.value }
        $bp = $null; $lk = $null
        $probe = Invoke-Probe $target
    } finally { $env:KAI_VAULT_PASS = $null; $env:KAI_VAULT_LNKEY = $null }
    $failed = @($probe | Where-Object { -not $_.pass })
    $verdict = if ($failed.Count -eq 0) { 'PASS' } else { 'FAIL' }
    $out = [ordered]@{ ts_utc = (Get-Date).ToUniversalTime().ToString('o'); generation = (Split-Path $target -Leaf); verdict = $verdict; checks = $probe.Count; failed = $failed.Count; results = $probe }
    foreach ($p in (Join-Path $VaultRoot 'status\restore_probe.json'), (Join-Path $LocalLogs 'restore_probe.json')) {
        $out | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $p -Encoding utf8
    }
    if ($verdict -eq 'PASS') { $null = Send-OffPiReceipt $target $verdict $probe.Count }
    Copy-Item -LiteralPath $Log -Destination (Join-Path $VaultRoot 'logs') -Force -EA SilentlyContinue
    Log "=== PROBE $verdict ($($probe.Count) Pruefungen, $($failed.Count) rot) ==="
    exit $(if ($verdict -eq 'PASS') { 0 } else { 1 })
}

# ── 20-h-Sperre ────────────────────────────────────────────────────────────
$last = Get-VerifiedGens | Select-Object -Last 1
if ($last -and -not $Force) {
    $age = (Get-Date).ToUniversalTime() - (ConvertFrom-GenName $last.Name)
    if ($age.TotalHours -lt $MinHoursBetweenRuns) {
        Log ('SKIP: letzte verifizierte Generation {0} ist {1:N1} h alt (< {2} h). -Force erzwingt einen Lauf.' -f $last.Name, $age.TotalHours, $MinHoursBetweenRuns)
        Remove-Item -LiteralPath $Log -Force -EA SilentlyContinue
        exit 0
    }
}

$GenDir = Join-Path $genRoot $Stamp
foreach ($d in 'pi', 'ln', 'laptop', 'laptop\git', 'laptop\keepass') { New-Item -ItemType Directory -Force -Path (Join-Path $GenDir $d) | Out-Null }
Set-Content -LiteralPath (Join-Path $GenDir '.incomplete') -Value $Stamp -Encoding ascii

if (Test-Path 'C:\Users\sasch\.local\bin\kai_wait_network.ps1') {
    . 'C:\Users\sasch\.local\bin\kai_wait_network.ps1'
    Log "Netz: $(Wait-KaiNetwork -HostName $PiHost)"
}

$bp = Get-BackupPassphrase
$lk = Get-LnKey
$PassSource = if ($bp) { $bp.source } else { 'MISSING' }
$LnSource = if ($lk) { $lk.source } else { 'MISSING' }
Log "Schluessel: KAI_BACKUP_PASSPHRASE <- $PassSource ; LN_SECRET_BACKUP_KEY <- $LnSource"
if ($bp) { $env:KAI_VAULT_PASS = $bp.value }
if ($lk) { $env:KAI_VAULT_LNKEY = $lk.value }
$bp = $null; $lk = $null

# Laptop-Quelle als tar | openssl direkt auf die Platte.
function New-EncTar {
    param([string]$Name, [string]$RelOut, [string]$BaseDir, [string[]]$Items = @(), [string[]]$Exclude = @(),
        [string]$Key = 'backup', [string]$ListFile)
    $out = Join-Path $GenDir $RelOut
    $passVar = if ($Key -eq 'ln') { 'KAI_VAULT_LNKEY' } else { 'KAI_VAULT_PASS' }
    if (-not [Environment]::GetEnvironmentVariable($passVar)) {
        Add-Result $Name 'PARTIAL' "Schluessel '$Key' fehlt -- NICHT gesichert (kein Rueckfall auf einen anderen Schluessel)"
        return
    }
    $ex = ($Exclude | ForEach-Object { "--exclude $(Q $_)" }) -join ' '
    $src = if ($ListFile) { "-T $(Q $ListFile)" } else { ($Items | ForEach-Object { Q $_ }) -join ' ' }
    $e1 = New-TempPath 'tar.err'; $e2 = New-TempPath 'ossl.err'
    $rc = Invoke-Cmd "$(Q $TarExe) -czf - -C $(Q $BaseDir) $ex $src 2>$(Q $e1) | $(Q $Ossl) $OpensslEnc -pass env:$passVar -out $(Q $out) 2>$(Q $e2)"
    $tarErr = @(Read-ErrLines $e1); $osslErr = @(Read-ErrLines $e2)
    if ($rc -ne 0 -or $osslErr.Count -gt 0 -or -not (Test-Path -LiteralPath $out) -or (Get-Item -LiteralPath $out).Length -lt 64) {
        Add-Result $Name 'FAIL' ('Verschluesselung gescheitert rc={0} {1}' -f $rc, ($osslErr -join ' | ')) $RelOut
        return
    }
    $sha = Get-Sha256 $out; $bytes = (Get-Item -LiteralPath $out).Length
    $extra = @{ key = $Key; kind = 'tar' }
    if ($tarErr.Count -gt 0) {
        $sample = ($tarErr | Select-Object -First 2) -join ' | '
        Add-Result $Name 'WARN' ('{0:N1} MB; {1} Datei(en) nicht lesbar (meist gesperrt), z. B.: {2}' -f ($bytes / 1MB), $tarErr.Count, $sample) $RelOut $bytes $sha $extra
    } else {
        Add-Result $Name 'OK' ('{0:N1} MB' -f ($bytes / 1MB)) $RelOut $bytes $sha $extra
    }
}

$piScriptFile = $null
try {
    # ── A) Pi: Zustand auf der Pi packen und verschluesseln ────────────────
    if ($SkipPi) {
        Add-Result 'pi_state' 'SKIPPED' '-SkipPi'
    } else {
        Log 'Pi: Zustand auf der Pi packen und verschluesseln (nice/ionice, SQLite-Backup-API)'
        $piScript = @'
#!/bin/bash
# kai_vault, Pi-Teil. Gegenueber dem Betrieb nur lesend: SQLite ueber die
# Backup-API (Quelle read-only), tar mit nice/ionice, Chiffrat nach
# /tmp/kai-vault-<stamp>. Kein Dienst, kein Restart. Klartext-Snapshots
# werden per trap entfernt, bevor die Verbindung endet.
STAMP="$1"; MODE="$2"
case "$STAMP" in ""|*[!0-9A-Za-z-]*) echo "VAULT_ERR bad_stamp"; exit 1;; esac
REPO=/home/ubuntu/ai_analyst_trading_bot
W="/tmp/kai-vault-$STAMP"
if [ "$MODE" = "cleanup" ]; then
  rm -rf "$W"
  find /tmp -maxdepth 1 -name 'kai-vault-*' -mmin +720 -exec rm -rf {} + 2>/dev/null
  echo "VAULT_CLEANED"; exit 0
fi
umask 077
rm -rf "$W"
mkdir -p "$W/consistent/data" "$W/consistent/artifacts" "$W/consistent/_pi_extras" || { echo "VAULT_ERR mkdir"; exit 1; }
trap 'rm -rf "$W/consistent"' EXIT
cd "$REPO" || { echo "VAULT_ERR repo"; exit 1; }
set -a; . ./.env >/dev/null 2>&1; set +a
[ -n "${KAI_BACKUP_PASSPHRASE:-}" ] || { echo "VAULT_ERR no_passphrase"; exit 2; }
ENC="openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -pass env:KAI_BACKUP_PASSPHRASE"
DEC="openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:KAI_BACKUP_PASSPHRASE"

python3 - "$REPO" "$W/consistent" <<'PY'
import os, sqlite3, sys
repo, out = sys.argv[1], sys.argv[2]
for rel in ("data/dev.db", "artifacts/premium_signal_events.sqlite3", "artifacts/tradingview_replay_cache.db"):
    src = os.path.join(repo, rel)
    if not os.path.exists(src):
        print(f"VAULT_NOTE sqlite_missing {rel}")
        continue
    dst = os.path.join(out, rel)
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    d = sqlite3.connect(dst)
    s.backup(d)
    d.close()
    s.close()
    c = sqlite3.connect(dst)
    chk = c.execute("PRAGMA quick_check").fetchone()[0]
    c.close()
    print(f"VAULT_SQLITE {rel} {chk} {os.path.getsize(dst)}")
PY
[ $? -eq 0 ] || { echo "VAULT_ERR sqlite_snapshot"; exit 1; }

X="$W/consistent/_pi_extras"
cp -p /home/ubuntu/*.py "$X/" 2>/dev/null
[ -f scripts/kai_operator_arm_backup.sh ] && cp -p scripts/kai_operator_arm_backup.sh "$X/"
crontab -l > "$X/crontab.txt" 2>/dev/null
systemctl list-unit-files 'kai-*' --no-legend > "$X/kai_unit_files.txt" 2>/dev/null
systemctl list-timers --all --no-legend > "$X/timers.txt" 2>/dev/null
{ git rev-parse HEAD; git status --porcelain; } > "$X/checkout_state.txt" 2>/dev/null
readlink -f /home/kai/current > "$X/release_current.txt" 2>/dev/null
uname -a > "$X/uname.txt" 2>/dev/null
dpkg-query -W -f='${Package} ${Version}\n' > "$X/dpkg_versions.txt" 2>/dev/null

SNAP=()
for f in data/dev.db artifacts/premium_signal_events.sqlite3 artifacts/tradingview_replay_cache.db; do
  [ -f "$W/consistent/$f" ] && SNAP+=("./consistent/$f")
done
SNAP+=("./consistent/_pi_extras")
nice -n 19 ionice -c3 tar -czf - --warning=no-file-changed --anchored \
  --exclude=./data/dev.db --exclude='./data/dev.db-*' --exclude='./data/dev.db.bak-*' \
  --exclude=./artifacts/premium_signal_events.sqlite3 --exclude='./artifacts/premium_signal_events.sqlite3-*' \
  --exclude=./artifacts/tradingview_replay_cache.db --exclude='./artifacts/tradingview_replay_cache.db-*' \
  --exclude=./artifacts/backups \
  --transform='s,^\./consistent/,./,' \
  -C "$REPO" ./data ./artifacts ./monitor/integrity ./DECISION_LOG.md ./.env \
  -C "$W" "${SNAP[@]}" | $ENC -out "$W/pi_state.tar.gz.enc"
rc=("${PIPESTATUS[@]}")
rm -rf "$W/consistent"
if [ "${rc[0]}" -gt 1 ] || [ "${rc[1]}" -ne 0 ]; then echo "VAULT_ERR pi_state tar=${rc[0]} openssl=${rc[1]}"; exit 1; fi
[ "${rc[0]}" -eq 1 ] && echo "VAULT_NOTE pi_state tar_rc=1 (Datei waehrend des Lesens gewachsen; append-only-Stroeme)"
$DEC -in "$W/pi_state.tar.gz.enc" | tar -tzf - > "$W/pi_state.list"; lrc=("${PIPESTATUS[@]}")
if [ "${lrc[0]}" -ne 0 ] || [ "${lrc[1]}" -ne 0 ]; then echo "VAULT_ERR pi_state_selfcheck ${lrc[*]}"; exit 1; fi
echo "VAULT_FILE pi_state pi_state.tar.gz.enc $(stat -c %s "$W/pi_state.tar.gz.enc") $(sha256sum "$W/pi_state.tar.gz.enc" | cut -d' ' -f1) $(wc -l < "$W/pi_state.list")"
rm -f "$W/pi_state.list"

ETC=$(ls -1t /mnt/kai-data/kai-standby/etc_*.tar.gz 2>/dev/null | head -1)
if [ -n "$ETC" ]; then
  n=$(tar -tzf "$ETC" 2>/dev/null | wc -l)
  $ENC -in "$ETC" -out "$W/pi_etc.tar.gz.enc" && \
    echo "VAULT_FILE pi_etc pi_etc.tar.gz.enc $(stat -c %s "$W/pi_etc.tar.gz.enc") $(sha256sum "$W/pi_etc.tar.gz.enc" | cut -d' ' -f1) $n $(basename "$ETC")"
else
  echo "VAULT_NOTE no_etc_tier"
fi

B=$(ls -1t artifacts/backups/*/kai_artifacts_*.tar.gz.enc 2>/dev/null | head -1)
if [ -n "$B" ]; then
  echo "VAULT_SRC pi_backup $REPO/$B $(stat -c %s "$B") $(sha256sum "$B" | cut -d' ' -f1)"
  [ -f "$B.manifest.json" ] && echo "VAULT_SRC pi_backup_manifest $REPO/$B.manifest.json $(stat -c %s "$B.manifest.json") $(sha256sum "$B.manifest.json" | cut -d' ' -f1)"
else
  echo "VAULT_NOTE no_pi_backup"
fi
echo "VAULT_COUNT ln_pi_secrets $(find /home/ubuntu/kai-secrets 2>/dev/null | wc -l)"
echo "VAULT_DONE"
exit 0
'@
        $piScriptFile = New-TempPath 'pi.sh'
        [IO.File]::WriteAllText($piScriptFile, ($piScript -replace "`r`n", "`n"), (New-Object Text.UTF8Encoding($false)))
        $piOut = New-TempPath 'pi.out'; $piErr = New-TempPath 'pi.err'
        $rc = Invoke-Cmd "$(Q $SshExe) -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30 $PI `"bash -s -- $Stamp prepare`" <$(Q $piScriptFile) >$(Q $piOut) 2>$(Q $piErr)"
        $lines = @()
        if (Test-Path -LiteralPath $piOut) { $lines = @(Get-Content -LiteralPath $piOut); Remove-Item -LiteralPath $piOut -Force -EA SilentlyContinue }
        $errLines = @(Read-ErrLines $piErr)
        foreach ($l in $lines) { if ($l -match '^VAULT_(NOTE|SQLITE|ERR)') { Log "  pi: $l" } }
        if ($errLines.Count) { Log ('  pi stderr ({0} Zeilen): {1}' -f $errLines.Count, (($errLines | Select-Object -First 3) -join ' | ')) }
        $script:LnPiCount = $null
        $c = $lines | Where-Object { $_ -match '^VAULT_COUNT ln_pi_secrets ' } | Select-Object -First 1
        if ($c) { $script:LnPiCount = [int](($c -split ' ')[2]) }
        if (-not ($lines -match '^VAULT_DONE')) {
            Add-Result 'pi_state' 'PARTIAL' ("Pi-Teil ohne VAULT_DONE (rc=$rc): " + (($lines | Where-Object { $_ -match '^VAULT_ERR' }) -join ' | '))
        } else {
            foreach ($l in ($lines | Where-Object { $_ -match '^VAULT_FILE ' })) {
                $p = $l -split ' '
                $name = $p[1]; $fileName = $p[2]; $srcSha = $p[4]; $members = [int]$p[5]
                $dst = Join-Path $GenDir "pi\$fileName"
                & $ScpExe -o BatchMode=yes -q "${PI}:/tmp/kai-vault-$Stamp/$fileName" $dst 2>&1 | Out-Null
                if (-not (Test-Path -LiteralPath $dst)) { Add-Result $name 'FAIL' 'scp fehlgeschlagen'; continue }
                $sha = Get-Sha256 $dst
                if ($sha -ne $srcSha) { Add-Result $name 'FAIL' ('sha256 Pi {0} != D: {1}' -f $srcSha.Substring(0, 16), $sha.Substring(0, 16)) "pi\$fileName"; continue }
                $extra = @{ key = 'backup'; kind = 'tar'; source_sha256 = $srcSha; members_source = $members }
                if ($p.Count -gt 6) { $extra['source_file'] = $p[6] }
                $bytes = (Get-Item -LiteralPath $dst).Length
                Add-Result $name 'OK' ('{0:N1} MB, sha = Pi, {1} Eintraege' -f ($bytes / 1MB), $members) "pi\$fileName" $bytes $sha $extra
            }
            foreach ($l in ($lines | Where-Object { $_ -match '^VAULT_SRC ' })) {
                $p = $l -split ' '
                $name = $p[1]; $srcPath = $p[2]; $srcSha = $p[4]
                $fileName = Split-Path $srcPath -Leaf
                $dst = Join-Path $GenDir "pi\$fileName"
                & $ScpExe -o BatchMode=yes -q "${PI}:$srcPath" $dst 2>&1 | Out-Null
                if (-not (Test-Path -LiteralPath $dst)) { Add-Result $name 'FAIL' 'scp fehlgeschlagen'; continue }
                $sha = Get-Sha256 $dst
                if ($sha -ne $srcSha) { Add-Result $name 'FAIL' 'sha256 Pi != D:' "pi\$fileName"; continue }
                $isEnc = $fileName -like '*.tar.gz.enc'
                $extra = @{ key = $(if ($isEnc) { 'backup' } else { 'none' }); kind = $(if ($isEnc) { 'tar' } else { 'plain' }); source_sha256 = $srcSha; source_path = $srcPath }
                Add-Result $name 'OK' "$fileName, sha = Pi" "pi\$fileName" (Get-Item -LiteralPath $dst).Length $sha $extra
            }
        }
        $null = Invoke-Cmd "$(Q $SshExe) -o BatchMode=yes -o ConnectTimeout=20 $PI `"bash -s -- $Stamp cleanup`" <$(Q $piScriptFile) >nul 2>nul"
    }

    # ── B) Lightning ───────────────────────────────────────────────────────
    if (-not $SkipPi) {
        $out = Join-Path $GenDir 'ln\ln_pi_secrets.tar.gz.enc'
        if (-not $env:KAI_VAULT_LNKEY) {
            Add-Result 'ln_pi_secrets' 'PARTIAL' 'LN_SECRET_BACKUP_KEY fehlt -- ~/kai-secrets NICHT gesichert (kein Rueckfall; einmalig: kai_vault.ps1 -StoreLnKey)'
        } else {
            $e1 = New-TempPath 'e1.txt'; $e2 = New-TempPath 'e2.txt'
            $rc = Invoke-Cmd "$(Q $SshExe) -n -o BatchMode=yes -o ConnectTimeout=20 $PI `"tar czf - -C /home/ubuntu kai-secrets`" 2>$(Q $e1) | $(Q $Ossl) $OpensslEnc -pass env:KAI_VAULT_LNKEY -out $(Q $out) 2>$(Q $e2)"
            $err = @(Read-ErrLines $e1) + @(Read-ErrLines $e2)
            if ($rc -ne 0 -or $err.Count -gt 0 -or -not (Test-Path -LiteralPath $out)) {
                Add-Result 'ln_pi_secrets' 'FAIL' ("rc=$rc " + (($err | Select-Object -First 2) -join ' | '))
            } else {
                $extra = @{ key = 'ln'; kind = 'tar' }
                if ($null -ne $script:LnPiCount) { $extra['members_source'] = $script:LnPiCount }
                Add-Result 'ln_pi_secrets' 'OK' 'Pi ~/kai-secrets, LN-Schluessel' 'ln\ln_pi_secrets.tar.gz.enc' (Get-Item -LiteralPath $out).Length (Get-Sha256 $out) $extra
            }
        }
    }
    if (Test-Path -LiteralPath $ScbSource) {
        $dst = Join-Path $GenDir 'ln\channel.backup'
        Copy-Item -LiteralPath $ScbSource -Destination $dst -Force
        $s1 = Get-Sha256 $ScbSource; $s2 = Get-Sha256 $dst
        if ($s1 -eq $s2) {
            Add-Result 'ln_scb' 'OK' ('SCB {0} B, Stand {1:yyyy-MM-dd HH:mm} (aus dem stuendlichen SCB-Pull)' -f (Get-Item $dst).Length, (Get-Item $ScbSource).LastWriteTime) 'ln\channel.backup' (Get-Item $dst).Length $s2 @{ key = 'none'; kind = 'plain'; source_sha256 = $s1 }
        } else { Add-Result 'ln_scb' 'FAIL' 'sha256 Quelle != Kopie' }
    } else { Add-Result 'ln_scb' 'PARTIAL' "fehlt: $ScbSource" }

    # ── C) Laptop ──────────────────────────────────────────────────────────
    if ($SkipLaptop) {
        Add-Result 'laptop' 'SKIPPED' '-SkipLaptop'
    } else {
        # Git: alle Refs (auch Branches ohne Upstream) als Bundle.
        $tmpB = New-TempPath 'kai-all.bundle'
        try {
            & $GitExe -C $MainRepo bundle create $tmpB --all 2>&1 | Out-Null
            $vOut = & $GitExe -C $MainRepo bundle verify $tmpB 2>&1 | Out-String
            $vRc = $LASTEXITCODE
            $heads = @(& $GitExe -C $MainRepo bundle list-heads $tmpB 2>$null).Count
            if ($vRc -ne 0 -or $heads -eq 0) {
                Add-Result 'laptop_git' 'FAIL' "bundle verify rc=$vRc heads=$heads"
            } else {
                $out = Join-Path $GenDir 'laptop\git\kai-all.bundle.enc'; $e2 = New-TempPath 'e2.txt'
                $rc = Invoke-Cmd "$(Q $Ossl) $OpensslEnc -pass env:KAI_VAULT_PASS -in $(Q $tmpB) -out $(Q $out) 2>$(Q $e2)"
                $null = Read-ErrLines $e2
                if ($rc -ne 0) { Add-Result 'laptop_git' 'FAIL' "openssl rc=$rc" }
                else { Add-Result 'laptop_git' 'OK' ('{0} Refs, {1:N1} MB' -f $heads, ((Get-Item $tmpB).Length / 1MB)) 'laptop\git\kai-all.bundle.enc' (Get-Item $out).Length (Get-Sha256 $out) @{ key = 'backup'; kind = 'bundle'; members_source = $heads } }
            }
        } finally { Remove-Item -LiteralPath $tmpB -Force -EA SilentlyContinue }

        # Nicht Committetes: Stashes und schmutzige Worktrees als Patch +
        # nicht ignorierte, ungetrackte Dateien.
        $stage = New-TempPath 'wip'
        New-Item -ItemType Directory -Force -Path $stage | Out-Null
        try {
            $nStash = @(& $GitExe -C $MainRepo stash list 2>$null).Count
            for ($i = 0; $i -lt $nStash; $i++) {
                # Meta als Text (PowerShell), Patch byte-sicher ueber cmd (Binaer-Hunks).
                $meta = & $GitExe -C $MainRepo log -1 --format='%H%n%gd%n%s' "stash@{$i}" 2>$null | Out-String
                Set-Content -LiteralPath (Join-Path $stage ('stash_{0:00}.meta.txt' -f $i)) -Value $meta -Encoding utf8
                $null = Invoke-Cmd "$(Q $GitExe) -C $(Q $MainRepo) stash show -p --binary --include-untracked stash@{$i} >$(Q (Join-Path $stage ('stash_{0:00}.patch' -f $i))) 2>nul"
            }
            $wts = @((& $GitExe -C $MainRepo worktree list --porcelain 2>$null) | Where-Object { $_ -like 'worktree *' } | ForEach-Object { $_.Substring(9) })
            $dirty = 0; $skippedBig = 0
            foreach ($wt in $wts) {
                if (-not (Test-Path -LiteralPath $wt)) { continue }
                $st = @(& $GitExe -C $wt status --porcelain 2>$null)
                if ($st.Count -eq 0) { continue }
                $dirty++
                $tag = ($wt -replace '[:\\/ ]+', '_').Trim('_')
                $d = Join-Path $stage "wt_$tag"; New-Item -ItemType Directory -Force -Path $d | Out-Null
                $head = & $GitExe -C $wt rev-parse HEAD 2>$null
                $br = & $GitExe -C $wt branch --show-current 2>$null
                Set-Content -LiteralPath (Join-Path $d 'META.txt') -Value "worktree=$wt`nhead=$head`nbranch=$br`n`n$($st -join "`n")" -Encoding utf8
                $null = Invoke-Cmd "$(Q $GitExe) -C $(Q $wt) diff HEAD --binary >$(Q (Join-Path $d 'tracked.patch')) 2>nul"
                foreach ($rel in @(& $GitExe -C $wt ls-files -o --exclude-standard 2>$null)) {
                    $srcF = Join-Path $wt $rel
                    if (-not (Test-Path -LiteralPath $srcF -PathType Leaf)) { continue }
                    if ((Get-Item -LiteralPath $srcF).Length -gt $MaxUntrackedBytes) { $skippedBig++; continue }
                    $dstF = Join-Path (Join-Path $d 'untracked') $rel
                    New-Item -ItemType Directory -Force -Path (Split-Path $dstF -Parent) | Out-Null
                    Copy-Item -LiteralPath $srcF -Destination $dstF -Force
                }
            }
            New-EncTar -Name 'laptop_wip' -RelOut 'laptop\laptop_wip.tar.gz.enc' -BaseDir $stage -Items @('.')
            $last = $Results[$Results.Count - 1]
            $last.detail = "$($last.detail); $nStash Stashes, $dirty schmutzige Worktrees, $skippedBig Dateien > 50 MB ausgelassen"
        } finally { Remove-Item -LiteralPath $stage -Recurse -Force -EA SilentlyContinue }

        New-EncTar -Name 'laptop_claude' -RelOut 'laptop\laptop_claude.tar.gz.enc' -BaseDir $UserHome -Items @('.claude') -Exclude @(
            '.claude/tmp', '.claude/cache', '.claude/paste-cache', '.claude/shell-snapshots', '.claude/session-env',
            '.claude/downloads', '.claude/.credentials.json', '.claude/.credentials.lock')
        New-EncTar -Name 'laptop_codex' -RelOut 'laptop\laptop_codex.tar.gz.enc' -BaseDir $UserHome -Items @('.codex') -Exclude @(
            '.codex/.sandbox-bin', '.codex/.sandbox', '.codex/.sandbox-secrets', '.codex/plugins', '.codex/.tmp',
            '.codex/tmp', '.codex/cache', '.codex/worktrees', '.codex/auth.json')

        # Betriebswerkzeuge: .kai, .local\bin-Skripte, Projekt-.claude, Task-Definitionen.
        New-Item -ItemType Directory -Force -Path $TaskExport | Out-Null
        Get-ChildItem -LiteralPath $TaskExport -Filter '*.xml' -EA SilentlyContinue | Remove-Item -Force -EA SilentlyContinue
        foreach ($t in (Get-ScheduledTask -TaskName 'KAI-*' -EA SilentlyContinue)) {
            try { Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Set-Content -LiteralPath (Join-Path $TaskExport "$($t.TaskName).xml") -Encoding utf8 } catch { }
        }
        $opsList = New-TempPath 'ops.txt'
        $ops = [System.Collections.Generic.List[string]]::new()
        if (Test-Path "$UserHome\.kai") { $ops.Add('.kai') }
        if (Test-Path "$UserHome\.local\bin\.claude") { $ops.Add('.local/bin/.claude') }
        Get-ChildItem -LiteralPath "$UserHome\.local\bin" -File -Force |
            Where-Object { $_.Extension -in '.ps1', '.py', '.sh', '.md', '.cmd', '.bat', '.json', '.txt', '.toml', '.yml', '.yaml', '.xml' } |
            ForEach-Object { $ops.Add(".local/bin/$($_.Name)") }
        [IO.File]::WriteAllLines($opsList, $ops, (New-Object Text.UTF8Encoding($false)))
        New-EncTar -Name 'laptop_ops' -RelOut 'laptop\laptop_ops.tar.gz.enc' -BaseDir $UserHome -ListFile $opsList -Exclude @('.kai/vault/*.dpapi')
        Remove-Item -LiteralPath $opsList -Force -EA SilentlyContinue

        New-EncTar -Name 'laptop_kaimirror' -RelOut 'laptop\laptop_kaimirror.tar.gz.enc' -BaseDir $UserHome -Items @('KAI-mirror') -Exclude @(
            'KAI-mirror/runtime-backups', 'KAI-mirror/env-backups', 'KAI-mirror/dist', 'KAI-mirror/dist.previous',
            'KAI-mirror/dist.stage-*', 'KAI-mirror/vault-logs', 'KAI-mirror/scripts/__pycache__')
        $desk = @($DesktopProjects | Where-Object { Test-Path -LiteralPath (Join-Path "$UserHome\Desktop" $_) })
        New-EncTar -Name 'laptop_desktop' -RelOut 'laptop\laptop_desktop.tar.gz.enc' -BaseDir "$UserHome\Desktop" -Items $desk
        if (Test-Path -LiteralPath "$MainRepo\.env") {
            New-EncTar -Name 'laptop_env' -RelOut 'laptop\laptop_env.tar.gz.enc' -BaseDir $MainRepo -Items @('.env')
        } else { Add-Result 'laptop_env' 'PARTIAL' 'Repo-.env fehlt' }

        # Authentisierungsmaterial nur mit dem LN-Schluessel.
        $auth = @('.ssh', '.codex/auth.json', '.codex/.sandbox-secrets', '.claude/.credentials.json') |
            Where-Object { Test-Path -LiteralPath (Join-Path $UserHome ($_ -replace '/', '\')) }
        New-EncTar -Name 'laptop_auth' -RelOut 'laptop\laptop_auth.tar.gz.enc' -BaseDir $UserHome -Items $auth -Key 'ln'

        # KeePass (eigenverschluesselt): neueste .kdbx.
        $kdbx = foreach ($d in $KeePassDirs) { if (Test-Path -LiteralPath $d) { Get-ChildItem -LiteralPath $d -Filter '*.kdbx' -File -EA SilentlyContinue } }
        $kdbx = @($kdbx | Sort-Object LastWriteTime -Descending)
        if ($kdbx.Count -eq 0) {
            Add-Result 'keepass' 'PARTIAL' 'keine .kdbx gefunden'
        } else {
            $k = $kdbx[0]
            $dst = Join-Path $GenDir "laptop\keepass\$($k.Name)"
            Copy-Item -LiteralPath $k.FullName -Destination $dst -Force
            $s1 = Get-Sha256 $k.FullName; $s2 = Get-Sha256 $dst
            $warn = if ($k.DirectoryName -like '*\Downloads') { ' -- liegt noch in Downloads (W0-1: an festen Ort verlegen)' } else { '' }
            if ($s1 -eq $s2) { Add-Result 'keepass' $(if ($warn) { 'WARN' } else { 'OK' }) ("$($k.Name), Stand {0:yyyy-MM-dd}$warn" -f $k.LastWriteTime) "laptop\keepass\$($k.Name)" $k.Length $s2 @{ key = 'none'; kind = 'plain'; source_sha256 = $s1 } }
            else { Add-Result 'keepass' 'FAIL' 'sha256 Quelle != Kopie' }
        }
    }

    # ── D) Statisch: RaspiBlitz-Image einmalig ─────────────────────────────
    if (-not $SkipStatic -and (Test-Path -LiteralPath $StaticImg)) {
        $sd = Join-Path $VaultRoot 'static\raspiblitz-image'
        $sums = Join-Path $sd 'SHA256SUMS.txt'
        if (-not (Test-Path -LiteralPath $sums)) {
            New-Item -ItemType Directory -Force -Path $sd | Out-Null
            $null = robocopy $StaticImg $sd /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
            $bad = 0; $lines = @()
            foreach ($f in (Get-ChildItem -LiteralPath $StaticImg -File -Recurse)) {
                $rel = $f.FullName.Substring($StaticImg.Length).TrimStart('\')
                $a = Get-Sha256 $f.FullName; $bPath = Join-Path $sd $rel
                $b = if (Test-Path -LiteralPath $bPath) { Get-Sha256 $bPath } else { '' }
                if ($a -ne $b) { $bad++ }
                $lines += "$a  $rel"
            }
            if ($bad -eq 0) { $lines | Set-Content -LiteralPath $sums -Encoding utf8; Log '[OK] static_raspiblitz_image -- einmalig kopiert, sha256 Quelle = Ziel' }
            else { Log "[WARN] static_raspiblitz_image -- $bad Datei(en) weichen ab, SHA256SUMS nicht geschrieben (naechster Lauf wiederholt)" }
        }
    }

    # ── E) Manifest + README ───────────────────────────────────────────────
    $manifest = [ordered]@{
        schema          = 'kai_vault_manifest/v1'
        version         = $VaultVersion
        generation      = $Stamp
        vault_id        = $v.id.vault_id
        host            = $env:COMPUTERNAME
        encryption      = 'openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 (unauthentifiziert, R10; Integritaet ueber sha256 in diesem Manifest + Probe)'
        key_backup      = "KAI_BACKUP_PASSPHRASE (KeePass; Quelle dieses Laufs: $PassSource)"
        key_ln          = "LN_SECRET_BACKUP_KEY (KeePass; Quelle dieses Laufs: $LnSource)"
        not_included    = @('channel.db/wallet.db (bewusst kein Restore-Pfad; nur SCB)', 'Wallet-Seed (Papier)', 'DPAPI-gebundene Laptop-Kopien (C:\Users\sasch\KAI-mirror\runtime-backups, env-backups)')
        sources         = $Results
    }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $GenDir 'MANIFEST.json') -Encoding utf8
    $rows = ($Results | Where-Object { $_.file } | ForEach-Object { '| `{0}` | {1} | {2} | {3} |' -f $_.file, $_.name, $_.status, $(if ($_.PSObject.Properties['key']) { $_.key } else { '' }) }) -join "`n"
    $readme = @"
# KAI-Vault -- Generation $Stamp

Erzeugt von ``$VaultVersion`` auf $env:COMPUTERNAME. Pruefsummen und Status je Quelle: ``MANIFEST.json``.
Probe-Ergebnis: ``VERIFIED.json`` (fehlt die Datei, ist diese Generation NICHT geprueft).

## Schluessel (beide in KeePass, NICHT auf dieser Platte)
- ``backup`` = **KAI_BACKUP_PASSPHRASE** (dieselbe wie fuer die Pi-Tagesarchive)
- ``ln`` = **LN_SECRET_BACKUP_KEY** (nur Authentisierungsmaterial: Pi ~/kai-secrets, .ssh, Codex-/Claude-Anmeldung)
- ``none`` = unverschluesselt: KeePass-DB (eigenverschluesselt), channel.backup (von LND verschluesselt), Pi-Backup-Manifest

## Dateien
| Datei | Quelle | Status | Schluessel |
|---|---|---|---|
$rows

## Entschluesseln (Windows: openssl aus Git for Windows; Linux/Pi: openssl)
``````
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in <datei>.tar.gz.enc -out <datei>.tar.gz
tar -xzf <datei>.tar.gz
``````
Die Passphrase fragt openssl interaktiv ab. Eine erfolgreiche Entschluesselung allein beweist NICHT die richtige
Passphrase (CBC ohne MAC, R10) -- erst ``tar -t`` bzw. der sha256-Vergleich mit MANIFEST.json.

## Wiederherstellung (Kurzform)
1. **Code:** ``kai-all.bundle.enc`` entschluesseln, ``git clone kai-all.bundle KAI`` (alle Refs), dann ``laptop_wip`` fuer Nicht-Committetes.
2. **Pi:** Ubuntu 24.04, Repo nach /home/ubuntu/ai_analyst_trading_bot, ``pi_state.tar.gz.enc`` entschluesseln und im Repo entpacken
   (data/, artifacts/, monitor/integrity/, DECISION_LOG.md, .env; SQLite-Dateien sind konsistente Snapshots).
   ``_pi_extras/`` enthaelt Unit-Liste, Timer, Checkout-/Release-Stand, crontab und unversionierte Skripte; ``pi_etc`` das /etc-Tier.
3. **Lightning:** ``channel.backup`` nur ueber die Wallet-Recovery mit dem Seed einspielen. NIE einen alten channel.db-Stand starten
   (Force-Close-/Penalty-Risiko). ``ln_pi_secrets`` enthaelt Macaroons, TLS und den HOTP-Seed (Schluessel ``ln``).
4. **Laptop:** ``laptop_claude`` (Skills, Plans, Memory, Projekte), ``laptop_codex``, ``laptop_ops`` (.kai, .local\bin-Skripte, Task-XML), ``laptop_kaimirror``, ``laptop_desktop``, ``laptop_env``, ``laptop_auth`` (Schluessel ``ln``).
"@
    Set-Content -LiteralPath (Join-Path $GenDir 'README-RESTORE.md') -Value $readme -Encoding utf8

    # ── F) Probe ───────────────────────────────────────────────────────────
    Log 'Probe: jede Datei gegen das Manifest, jedes Archiv entschluesseln und listen, Tiefenprobe pi_state'
    $probe = Invoke-Probe $GenDir
    $probeFailed = @($probe | Where-Object { -not $_.pass })
    $probeVerdict = if ($probe.Count -gt 0 -and $probeFailed.Count -eq 0) { 'PASS' } else { 'FAIL' }
    $probeOut = [ordered]@{ ts_utc = (Get-Date).ToUniversalTime().ToString('o'); generation = $Stamp; verdict = $probeVerdict; checks = $probe.Count; failed = $probeFailed.Count; results = $probe }
    foreach ($p in (Join-Path $VaultRoot 'status\restore_probe.json'), (Join-Path $LocalLogs 'restore_probe.json')) {
        $probeOut | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $p -Encoding utf8
    }
    if ($probeVerdict -eq 'PASS') {
        $probeOut | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $GenDir 'VERIFIED.json') -Encoding utf8
        Get-ChildItem -LiteralPath $GenDir -File -Recurse | ForEach-Object { $_.IsReadOnly = $true }
        Remove-Item -LiteralPath (Join-Path $GenDir '.incomplete') -Force -EA SilentlyContinue
    } else {
        $probeOut | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $GenDir 'PROBE_FAILED.json') -Encoding utf8
    }

    # ── G) Aufbewahrung (nur nach frisch verifizierter Generation) ─────────
    $removed = @()
    if ($probeVerdict -eq 'PASS') {
        $verified = @(Get-VerifiedGens | ForEach-Object { $_.Name })
        $keep = Get-GfsKeep -Verified $verified
        foreach ($g in (Get-ChildItem -LiteralPath $genRoot -Directory | Where-Object { Test-GenName $_.Name })) {
            if ($g.Name -eq $Stamp) { continue }
            $isVerified = Test-Path -LiteralPath (Join-Path $g.FullName 'VERIFIED.json')
            $drop = $false
            if ($isVerified -and ($keep -notcontains $g.Name)) { $drop = $true }
            if (-not $isVerified -and ((Get-Date).ToUniversalTime() - (ConvertFrom-GenName $g.Name)).TotalDays -gt 7) { $drop = $true }
            if ($drop) {
                Get-ChildItem -LiteralPath $g.FullName -File -Recurse | ForEach-Object { $_.IsReadOnly = $false }
                Remove-Item -LiteralPath $g.FullName -Recurse -Force
                $removed += $g.Name
            }
        }
        if ($removed) { Log "Aufbewahrung: entfernt $($removed -join ', ')" }
    }
} catch {
    # Kein stiller Abbruch: Ausnahme protokollieren, Generation bleibt .incomplete,
    # Urteil unten wird FATAL (Probe nicht bestanden).
    Log "FATAL: Ausnahme im Lauf: $($_.Exception.Message) @ $($_.InvocationInfo.ScriptLineNumber)"
    Add-Result 'run' 'FAIL' "Ausnahme: $($_.Exception.Message)"
    if (-not $probeVerdict) { $probeVerdict = 'NOT_RUN'; $probe = @(); $probeFailed = @() }
} finally {
    $env:KAI_VAULT_PASS = $null
    $env:KAI_VAULT_LNKEY = $null
    if ($piScriptFile) { Remove-Item -LiteralPath $piScriptFile -Force -EA SilentlyContinue }
}

# ── H) Urteil, Status, Quittung ───────────────────────────────────────────
$fail = @($Results | Where-Object { $_.status -eq 'FAIL' })
$part = @($Results | Where-Object { $_.status -in 'PARTIAL', 'SKIPPED' })
$warn = @($Results | Where-Object { $_.status -eq 'WARN' })
$exitCode = if ($fail.Count -gt 0 -or $probeVerdict -ne 'PASS') { 1 } elseif ($part.Count -gt 0) { 2 } else { 0 }
$verdict = @{ 0 = 'OK'; 1 = 'FATAL'; 2 = 'PARTIAL' }[$exitCode]
$receiptState = if ($probeVerdict -eq 'PASS') { Send-OffPiReceipt $GenDir $probeVerdict $probe.Count } else { 'SKIPPED_PROBE_FAIL' }
$genBytes = (Get-ChildItem -LiteralPath $GenDir -File -Recurse | Measure-Object Length -Sum).Sum
$vols = if ($v.volume) { Get-Volume -DriveLetter $v.volume.DriveLetter } else { $null }
$status = @{
    exit = $exitCode; verdict = $verdict; generation = $Stamp; vault_id = $v.id.vault_id
    probe = $probeVerdict; probe_checks = $probe.Count; probe_failed = $probeFailed.Count
    sources_ok = @($Results | Where-Object { $_.status -eq 'OK' }).Count; warn = @($warn | ForEach-Object { $_.name })
    partial = @($part | ForEach-Object { $_.name }); fail = @($fail | ForEach-Object { $_.name })
    generation_gb = [math]::Round($genBytes / 1GB, 2); free_gb = $(if ($vols) { [math]::Round($vols.SizeRemaining / 1GB, 1) } else { $null })
    verified_generations = @(Get-VerifiedGens | ForEach-Object { $_.Name }); removed = $removed
    key_backup_source = $PassSource; key_ln_source = $LnSource; pi_receipt = $receiptState
}
Write-Status $status
$receipt = [ordered]@{
    ts_utc = (Get-Date).ToUniversalTime().ToString('o'); ort = 'KAI-VAULT (D:, offline/offsite)'; vault_id = $v.id.vault_id
    generation = $Stamp; verdict = $verdict; probe = $probeVerdict
    pi_state_sha256 = ($Results | Where-Object { $_.name -eq 'pi_state' } | Select-Object -First 1).sha256
    pi_backup_sha256 = ($Results | Where-Object { $_.name -eq 'pi_backup' } | Select-Object -First 1).sha256
}
Add-Content -LiteralPath (Join-Path $LocalLogs 'receipts.jsonl') -Value ($receipt | ConvertTo-Json -Compress) -Encoding utf8
Log ('=== {0} (exit {1}) -- Generation {2}, {3:N2} GB, Probe {4} {5}/{6} ===' -f $verdict, $exitCode, $Stamp, ($genBytes / 1GB), $probeVerdict, ($probe.Count - $probeFailed.Count), $probe.Count)
if ($part.Count) { Log ('Teilweise: ' + (($part | ForEach-Object { "$($_.name) ($($_.detail))" }) -join '; ')) }
Copy-Item -LiteralPath $Log -Destination (Join-Path $VaultRoot 'logs') -Force -EA SilentlyContinue
Get-ChildItem -LiteralPath $LocalLogs -Filter 'vault_*.log' | Sort-Object Name -Descending | Select-Object -Skip 60 | Remove-Item -Force -EA SilentlyContinue

switch ($exitCode) {
    0 { Show-Toast 'KAI-Vault OK' "Generation $Stamp geprueft. Platte sicher entfernen und getrennt verwahren." }
    2 { Show-Toast 'KAI-Vault TEILWEISE' ("Geprueft, aber unvollstaendig: " + (($part | ForEach-Object { $_.name }) -join ', ') + '. Details: KAI-mirror\vault-logs') }
    default { Show-Toast 'KAI-Vault FEHLER' ("Generation NICHT verifiziert. Log: KAI-mirror\vault-logs\vault_$Stamp.log") }
}
exit $exitCode
