# Backup Restore Drill

Der Drill prueft monatlich, ob das verschluesselte Artifact-Backup nicht nur
geschrieben wurde, sondern auch entschluesselt, entpackt und gegen den aktuellen
Backup-Contract gelesen werden kann.

## Ablauf

1. `kai-backup-artifacts.timer` schreibt taeglich um 03:40 lokal ein
   `artifacts/backups/<YYYY-MM-DD>/kai_artifacts_<UTC>.tar.gz.enc`.
2. `kai-backup-restore-drill.timer` startet monatlich am 01. um 04:10 lokal
   `scripts/kai_backup_restore_drill.sh`.
3. Das Drill-Skript nimmt ohne Parameter das neueste `kai_artifacts_*.tar.gz.enc`
   oder mit `--archive <pfad>` ein explizites Archiv.
4. Es entschluesselt mit `KAI_BACKUP_PASSPHRASE` in ein `mktemp -d`-Verzeichnis,
   entpackt dort, validiert erwartete Dateien, schreibt das Beweis-Artefakt und
   raeumt das temporaere Verzeichnis per `trap` wieder auf.

## Beweis-Artefakt

Jeder Pfad schreibt `artifacts/ops/backup_drill/<UTC-ts>.json` mit festem Schema:

```json
{
  "schema": "backup_restore_drill/v1",
  "ts_utc": "2026-08-25T02:10:00Z",
  "status": "PASS",
  "reason": "ok",
  "archive": "/home/ubuntu/ai_analyst_trading_bot/artifacts/backups/2026-08-25/kai_artifacts_2026-08-25T02-40-00Z.tar.gz.enc",
  "archive_sha256": "<sha256>",
  "files_expected": ["artifacts/research/prereg_ledger.jsonl"],
  "files_restored": ["artifacts/research/prereg_ledger.jsonl"],
  "files_missing": [],
  "sha256_mismatch": [],
  "duration_s": 2,
  "host": "kai-pi5"
}
```

Wenn `backup_audit.jsonl` spaeter per-file-Hashes schreibt, nutzt der Drill diese
als Erwartung. Bis dahin nimmt er die aktuelle explizite Quellliste aus
`scripts/kai_backup_artifacts.sh`, erweitert vorhandene Quellverzeichnisse auf
Dateien und vergleicht Restore-Dateien gegen die aktuellen SHA-256-Werte. JSON
und JSONL muessen parsebar und nicht leer sein.

## Exit-Codes

- `0`: PASS.
- `2`: `KAI_BACKUP_PASSPHRASE` fehlt; FAIL-Artefakt wurde geschrieben.
- `3`: kein Archiv vorhanden; FAIL-Artefakt wurde geschrieben.
- `4`: Entschluesselung, Entpacken oder Laufzeit-Voraussetzung fehlgeschlagen.
- `6`: Inhalt weicht ab, z. B. fehlende Datei, leere/ungueltige JSON-Datei oder
  SHA-256-Abweichung.

Jeder Nicht-Null-Exit faerbt die systemd-Unit rot und triggert
`kai-unit-failure-notify@%n.service`.

## Manuell fahren

```bash
KAI_BACKUP_PASSPHRASE=... bash scripts/kai_backup_restore_drill.sh
KAI_BACKUP_PASSPHRASE=... bash scripts/kai_backup_restore_drill.sh --archive artifacts/backups/2026-08-25/kai_artifacts_2026-08-25T02-40-00Z.tar.gz.enc
```

Ein FAIL bedeutet: Das Backup ist fuer Restore-Zwecke nicht bewiesen. Operator
prueft zuerst das neueste Beweis-Artefakt, danach `journalctl -u
kai-backup-restore-drill.service` und `artifacts/kai_backup.log`.

Ab STAB-08 wird ein fehlendes oder zu altes Drill-Artefakt als Health-Befund
gemeldet; diese Health-Integration ist hier bewusst noch nicht gebaut.
