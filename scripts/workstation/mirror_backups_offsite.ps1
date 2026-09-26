# Quelle: Repo scripts/workstation/mirror_backups_offsite.ps1 (MindBlow 2.0, W0-8). Hier NICHT editieren --
# im Repo aendern, dann: pwsh -File scripts/workstation/install_workstation.ps1 -Apply
# Local SECOND COPY of the ENCRYPTED KAI backup blobs (2026-06-13; relabelled 2026-09-25).
#
# NOT OFFSITE (MindBlow 2.0, W0-5, operator decision 25.09.2026): the target
# $env:OneDrive\KAI-Backups is a plain folder on C:. The OneDrive client has not
# synced since 17.07.2025 (not running, autostart off), so this copy sits on the
# SAME disk as its source. It only guards against accidental deletion/corruption
# inside KAI-mirror. The real offline/offsite copy is the KAI-Vault on the
# "KAI Backup" disk (C:\Users\sasch\KAI-mirror\scripts\kai_vault.ps1), encrypted
# with KAI_BACKUP_PASSPHRASE and therefore NOT bound to this PC's DPAPI.
# Runs after the daily full backup.
#
# SAFE TO PUT IN CLOUD: only ciphertext (*.aes, AES-256-CBC), DPAPI-wrapped keys
# (*.key.dpapi -- unwrappable ONLY by user sasch on THIS machine) and manifests
# (no secrets) are mirrored. No plaintext .env or ledger ever leaves encrypted.
#
# RECOVERY-SCOPE CAVEAT (honest): because the keys are DPAPI-CurrentUser-bound,
# the OneDrive copy is decryptable ONLY on this same Windows machine/user. It
# protects against: Pi loss + local KAI-mirror disk loss / corruption / crypto-
# locker. It does NOT yet protect against simultaneous loss of THIS PC -- that
# needs the separate passphrase-escrow add-on (still open, operator decision).
#
# Exit codes: 0 = mirrored OK; 1 = failure (alert marker set).

$ErrorActionPreference = 'Continue'
$OneDrive = $env:OneDrive
$Pairs = @(
    @{ Src = 'C:\Users\sasch\KAI-mirror\env-backups';     Name = 'env-backups' }
    @{ Src = 'C:\Users\sasch\KAI-mirror\runtime-backups'; Name = 'runtime-backups' }
    # LN SCB: only the DPAPI-AES ciphertext set (scb_backup_*.aes/.key.dpapi/
    # .manifest.json) -- cloud-safe. The plaintext history in KAI-mirror\
    # lightning-scb\ is deliberately NOT mirrored offsite (stays local).
    @{ Src = 'C:\Users\sasch\KAI-mirror\scb-backups';     Name = 'scb-backups' }
)
$DestRoot    = Join-Path $OneDrive 'KAI-Backups'
$Ts          = Get-Date -Format 'yyyyMMdd_HHmmss'
$Log         = Join-Path 'C:\Users\sasch\KAI-mirror' 'offsite_mirror.log'
$AlertMarker = Join-Path 'C:\Users\sasch\KAI-mirror' '_ALERT_OFFSITE_MIRROR_FAILED.txt'

function Log($m) { "$([DateTime]::Now.ToString('s'))  $m" | Tee-Object -FilePath $Log -Append | Out-Null }
function Fail($reason) {
    "OFFSITE MIRROR FAILED $Ts`nscript=mirror_backups_offsite.ps1`nreason=$reason`nRemoved automatically by next successful run." |
        Out-File -FilePath $AlertMarker -Encoding utf8
    Log "VERDICT: [FAIL] $reason (alert marker set)"
    exit 1
}

Log "=== offsite mirror start ($Ts) ==="
if (-not $OneDrive -or -not (Test-Path $OneDrive)) { Fail "OneDrive folder not available (`$env:OneDrive=$OneDrive)" }
New-Item -ItemType Directory -Force -Path $DestRoot | Out-Null

$hadError = $false
foreach ($p in $Pairs) {
    if (-not (Test-Path $p.Src)) { Log "[skip] source missing: $($p.Src)"; continue }
    $dest = Join-Path $DestRoot $p.Name
    # /MIR keeps dest == src (propagates retention deletions). /R:2 /W:2 tolerate
    # OneDrive mid-sync locks. Encrypted blobs only -> cloud-safe.
    # /XF *.tmp: in-flight backup archives (tar.gz.tmp) are exclusively locked by
    # the writing backup task (sharing violation rc>=8, seen 2026-07-11 12:27);
    # the finished blob is picked up on the next run after its rename.
    & robocopy $p.Src $dest /MIR /R:2 /W:2 /NP /NDL /NJH /NJS /XF '~$*' '*.tmp' 2>&1 |
        Out-File -FilePath $Log -Append -Encoding utf8
    $rc = $LASTEXITCODE
    if ($rc -ge 8) { Log "robocopy ERROR rc=$rc for $($p.Name)"; $hadError = $true }
    else {
        $n = (Get-ChildItem $dest -File -EA SilentlyContinue).Count
        Log "mirrored $($p.Name): $n files (robocopy rc=$rc)"
    }
}
if ($hadError) { Fail "one or more robocopy mirrors failed (rc>=8)" }

if (Test-Path $AlertMarker) { Remove-Item $AlertMarker -Force -EA SilentlyContinue }
Log "=== local second copy DONE -> $DestRoot (same disk C:, NOT offsite; offsite = KAI-Vault on 'KAI Backup') ==="
exit 0
