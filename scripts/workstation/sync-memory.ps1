# Quelle: Repo scripts/workstation/sync-memory.ps1 (MindBlow 2.0, W0-8). Hier NICHT editieren --
# im Repo aendern, dann: pwsh -File scripts/workstation/install_workstation.ps1 -Apply
# KAI Memory-Mirror - robocopy /MIR of Claude auto-memory to local backup.
# Quellen (je eigenes Ziel, weil /MIR je Quelle spiegelt):
#   C:\Users\sasch\.claude\projects\C--Users-sasch\memory\            -> KAI-mirror\memory\
#       (seit 01.09. MEMORY_WRITE_FREEZE; bleibt gespiegelt)
#   C:\Users\sasch\.claude\projects\C--Users-sasch--local-bin\memory\ -> KAI-mirror\memory-local-bin\
#       (das AKTIVE Gedaechtnis der Sitzungen unter .local\bin; lief bis 25.09. in
#        keinem Backup mit -- MindBlow 2.0, W0-5)
# Pattern: *.md + MEMORY.md (index) + *.sh (Operator-Skripte im Memory). No subdirs expected.
# Ausgefuehrt taeglich 03:15 via Scheduled Task "KAI-Memory-Mirror-Daily".
# Eigenes Log: Catch-up nach einem Boot kann beide Tasks gleichzeitig starten.
# Offline/offsite (versioniert, verschluesselt) sichert zusaetzlich kai_vault.ps1
# das ganze .claude-Verzeichnis auf die Platte "KAI Backup".

$ErrorActionPreference = "Stop"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$logFile = "C:\Users\sasch\KAI-mirror\sync-memory.log"
$pairs = @(
    @{ Source = "C:\Users\sasch\.claude\projects\C--Users-sasch\memory"; Mirror = "C:\Users\sasch\KAI-mirror\memory" },
    @{ Source = "C:\Users\sasch\.claude\projects\C--Users-sasch--local-bin\memory"; Mirror = "C:\Users\sasch\KAI-mirror\memory-local-bin" }
)

function Write-Log($msg) {
    # UTF-8 explizit: Tee-Object default = UTF-16LE unter PS5.1. Das getrennte
    # Log verhindert zusaetzlich Schreibsperren bei gleichzeitigem Task-Catch-up.
    $line = "$ts  $msg"
    Add-Content -Path $logFile -Value $line -Encoding utf8
    Write-Host $line
}

Write-Log "=== Memory-Sync START ==="

try {
    foreach ($p in $pairs) {
        $source = $p.Source; $mirror = $p.Mirror
        if (-not (Test-Path $source)) {
            throw "Source missing: $source"
        }
        if (-not (Test-Path $mirror)) {
            New-Item -ItemType Directory -Path $mirror -Force | Out-Null
            Write-Log "Mirror created: $mirror"
        }

        # robocopy exit codes: 0=no copy, 1=copied OK, 2=extra files, 3=both. >=8 = error.
        & robocopy $source $mirror *.md MEMORY.md *.sh /MIR /R:2 /W:2 /NP /NDL /NJH /NJS 2>&1 |
            Out-File -FilePath $logFile -Append -Encoding utf8
        $rc = $LASTEXITCODE
        if ($rc -ge 8) {
            throw "robocopy failed with exit-code $rc ($source)"
        }

        $fileCount = (Get-ChildItem -Path $mirror -File).Count
        $totalKB   = "{0:N1}" -f ((Get-ChildItem -Path $mirror -File | Measure-Object -Property Length -Sum).Sum / 1KB)
        Write-Log "Memory-Sync complete: $source -> $mirror : $fileCount files, $totalKB KB (robocopy rc=$rc)"
    }
    Write-Log "=== Memory-Sync DONE ==="
    # robocopy rc 1/2/3 sind ERFOLG (Dateien kopiert/extra), aber PowerShell wuerde
    # sonst $LASTEXITCODE (=robocopy-rc) als Prozess-Exit propagieren -> Scheduler
    # sieht faelschlich "Fehler". Nach erfolgreichem Sync explizit 0 zurueckgeben.
    exit 0
}
catch {
    Write-Log "ERROR: $_"
    exit 1
}
