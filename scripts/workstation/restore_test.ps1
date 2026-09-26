# Quelle: Repo scripts/workstation/restore_test.ps1 (MindBlow 2.0, W0-8). Hier NICHT editieren --
# im Repo aendern, dann: pwsh -File scripts/workstation/install_workstation.ps1 -Apply
# Automated restore-test of the off-Pi KAI backups (2026-06-13).
# A backup you have never restored is a hope, not a backup. This proves the FULL
# round-trip weekly: decrypt (DPAPI) -> untar -> content sanity -> verdict.
# Catches what creation-time self-verify cannot: DPAPI key rot, partial/ògz files,
# unreadable archives, empty/garbage ledgers, schema surprises.
#
# Scope: newest runtime_full + runtime_ledgers + env_backup sets. Read-only on the
# backups; everything lands in a throwaway temp dir that is wiped at the end.
#
# Exit: 0 = all checks passed; 1 = at least one FAIL (alert marker set).

$ErrorActionPreference = 'Continue'
$BkRuntime = 'C:\Users\sasch\KAI-mirror\runtime-backups'
$BkEnv     = 'C:\Users\sasch\KAI-mirror\env-backups'
$OutDir    = 'C:\Users\sasch\KAI-mirror\restore-tests'
$Ts        = Get-Date -Format 'yyyyMMdd_HHmmss'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Log         = Join-Path $OutDir "restore_test_$Ts.log"
$StatusJson  = Join-Path $OutDir 'latest_status.json'
$AlertMarker = Join-Path $OutDir '_ALERT_RESTORE_TEST_FAILED.txt'
$Work        = Join-Path $env:TEMP "kai_restore_test_$Ts"
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$results = [System.Collections.Generic.List[object]]::new()
function Log($m) { "$([DateTime]::Now.ToString('s'))  $m" | Tee-Object -FilePath $Log -Append | Out-Null }
function Check($name, $ok, $detail) {
    $results.Add([pscustomobject]@{ check = $name; pass = [bool]$ok; detail = "$detail" })
    Log ("[{0}] {1} -- {2}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail)
}

Add-Type -AssemblyName System.Security.Cryptography.ProtectedData -EA SilentlyContinue

# Decrypt a <stem><cipherExt> + <stem>.key.dpapi pair to raw plaintext bytes.
# cipherExt differs: runtime tarballs use '.tar.gz.aes', env uses '.aes'.
function Decrypt-Set($dir, $stem, $cipherExt) {
    $kiv = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes("$dir\$stem.key.dpapi"), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    $aes = [Security.Cryptography.Aes]::Create(); $aes.Key = $kiv[0..31]; $aes.IV = $kiv[32..47]
    $ct  = [IO.File]::ReadAllBytes("$dir\$stem$cipherExt")
    $pt  = $aes.CreateDecryptor().TransformFinalBlock($ct, 0, $ct.Length)
    [Array]::Clear($kiv, 0, $kiv.Length)
    return $pt
}

# Untar a decrypted tar.gz (bytes) into $Work\$tag, return the extraction dir.
function Extract-Tar($bytes, $tag) {
    $tgz = Join-Path $Work "$tag.tar.gz"
    [IO.File]::WriteAllBytes($tgz, $bytes)
    $dst = Join-Path $Work $tag
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    tar xzf $tgz -C $dst 2>&1 | Out-File $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "tar exit $LASTEXITCODE" }
    return $dst
}

function Test-Jsonl($path, $minLines) {
    if (-not (Test-Path $path)) { return @{ ok = $false; detail = "missing: $(Split-Path $path -Leaf)" } }
    $lines = (Get-Content $path | Measure-Object -Line).Lines
    if ($lines -lt $minLines) { return @{ ok = $false; detail = "$(Split-Path $path -Leaf): only $lines lines (<$minLines)" } }
    $last = (Get-Content $path | Where-Object { $_.Trim() } | Select-Object -Last 1)
    try { $null = $last | ConvertFrom-Json; return @{ ok = $true; detail = "$(Split-Path $path -Leaf): $lines lines, last line valid JSON" } }
    catch { return @{ ok = $false; detail = "$(Split-Path $path -Leaf): last line not JSON" } }
}

Log "=== restore-test start ($Ts) ==="

# --- 1) runtime FULL: decrypt -> untar -> dev.db header + ledger sanity ---
try {
    $stem = (Get-ChildItem $BkRuntime -Filter 'runtime_full_*.tar.gz.aes' | Sort-Object Name | Select-Object -Last 1).Name -replace '\.tar\.gz\.aes$', ''
    Check 'full.set_present' $true "newest: $stem"
    $dir = Extract-Tar (Decrypt-Set $BkRuntime $stem '.tar.gz.aes') 'full'
    $db = Join-Path $dir 'data\dev.db'
    if (Test-Path $db) {
        $hdr = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($db)[0..14])
        Check 'full.dev_db_sqlite' ($hdr -eq 'SQLite format 3') "header='$hdr', $([math]::Round((Get-Item $db).Length/1MB,1)) MB"
        # Full integrity_check via Python's sqlite3 module (no sqlite3 CLI on this
        # laptop; until 2026-09-25 this silently degraded to a header-only check).
        $py = Get-Command python -EA SilentlyContinue
        if ($py) {
            $ic = (& $py.Source -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()" $db 2>&1 | Select-Object -Last 1)
            Check 'full.dev_db_integrity' ("$ic" -eq 'ok') "PRAGMA integrity_check=$ic (python sqlite3)"
        } else { Check 'full.dev_db_integrity' $false 'python not on PATH -> integrity_check impossible' }
    } else { Check 'full.dev_db_sqlite' $false 'data\dev.db missing in archive' }
    $r = Test-Jsonl (Join-Path $dir 'artifacts\paper_execution_audit.jsonl') 1; Check 'full.paper_audit_jsonl' $r.ok $r.detail
} catch { Check 'full.restore' $false "exception: $_" }

# --- 2) runtime LEDGERS: decrypt -> untar -> precious-n ledgers + session ---
try {
    $stem = (Get-ChildItem $BkRuntime -Filter 'runtime_ledgers_*.tar.gz.aes' | Sort-Object Name | Select-Object -Last 1).Name -replace '\.tar\.gz\.aes$', ''
    Check 'ledgers.set_present' $true "newest: $stem"
    $dir = Extract-Tar (Decrypt-Set $BkRuntime $stem '.tar.gz.aes') 'ledgers'
    foreach ($f in 'paper_execution_audit.jsonl', 'shadow_candidate_resolved.jsonl') {
        $r = Test-Jsonl (Join-Path $dir "artifacts\$f") 1; Check "ledgers.$f" $r.ok $r.detail
    }
    $sess = Join-Path $dir 'artifacts\telegram_channel.session'
    Check 'ledgers.session' ((Test-Path $sess) -and ((Get-Item $sess).Length -gt 0)) "session $([bool](Test-Path $sess)), $([int]((Get-Item $sess -EA SilentlyContinue).Length)) bytes"
} catch { Check 'ledgers.restore' $false "exception: $_" }

# --- 3) env: decrypt -> sha256 vs manifest + secrets sanity ---
try {
    $stem = (Get-ChildItem $BkEnv -Filter 'env_backup_*.aes' | Sort-Object Name | Select-Object -Last 1).Name -replace '\.aes$', ''
    $pt   = Decrypt-Set $BkEnv $stem '.aes'
    $sha  = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($pt)).Replace('-', '').ToLower()
    $man  = Get-Content "$BkEnv\$stem.manifest.json" -Raw | ConvertFrom-Json
    Check 'env.sha256_matches_manifest' ($sha -eq $man.plaintext_sha256) "decrypt sha256 vs manifest"
    $txt = [Text.Encoding]::UTF8.GetString($pt)
    Check 'env.sanity' ($txt -match 'EXECUTION_ENTRY_MODE=') "contains EXECUTION_ENTRY_MODE"
    [Array]::Clear($pt, 0, $pt.Length)
} catch { Check 'env.restore' $false "exception: $_" }

# --- verdict + cleanup ---
Remove-Item $Work -Recurse -Force -EA SilentlyContinue
$failed = @($results | Where-Object { -not $_.pass })
$verdict = if ($failed.Count -eq 0) { 'PASS' } else { 'FAIL' }
[ordered]@{ timestamp = $Ts; verdict = $verdict; checks_total = $results.Count; checks_failed = $failed.Count; results = $results } |
    ConvertTo-Json -Depth 5 | Out-File $StatusJson -Encoding utf8

# retention: keep newest 12 logs
Get-ChildItem $OutDir -Filter 'restore_test_*.log' | Sort-Object Name -Descending | Select-Object -Skip 12 | Remove-Item -Force -EA SilentlyContinue

if ($verdict -eq 'FAIL') {
    "RESTORE TEST FAILED $Ts`n$($failed | ForEach-Object { "- $($_.check): $($_.detail)" } | Out-String)Removed automatically by next passing run." |
        Out-File $AlertMarker -Encoding utf8
    Log "=== VERDICT: FAIL ($($failed.Count)/$($results.Count) checks) ==="
    exit 1
}
if (Test-Path $AlertMarker) { Remove-Item $AlertMarker -Force -EA SilentlyContinue }
Log "=== VERDICT: PASS ($($results.Count) checks) ==="
exit 0
