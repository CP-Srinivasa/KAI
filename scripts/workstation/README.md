# Workstation-Skripte (Laptop)

MindBlow 2.0, W0-8. Die Backup- und Vault-Werkzeuge des Laptops sind hier versioniert und getestet
(`tests/unit/test_workstation_scripts.py`). Die Scheduled Tasks rufen weiterhin die **Betriebskopien**
unter `%USERPROFILE%`. Der Installer bringt beide Seiten in Deckung:

```powershell
pwsh -File scripts/workstation/install_workstation.ps1          # Bericht: SAME / DRIFT / MISSING, Exit 3 bei Abweichung
pwsh -File scripts/workstation/install_workstation.ps1 -Apply   # installieren (vorher .bak-<stempel> der alten Kopie)
```

| Datei | Betriebsort | Aufgabe |
|---|---|---|
| `kai_vault.ps1` | `KAI-mirror\scripts\` | Offsite-Kopie auf die Platte „KAI Backup“: Generationen, `KAI_BACKUP_PASSPHRASE`-Verschlüsselung (Auth-Material mit `LN_SECRET_BACKUP_KEY`), Probe je Generation, Quittung auf der Pi (`app/observability/offpi_receipts.py`) |
| `kai_vault_legacy_encrypt.ps1` | `KAI-mirror\scripts\` | Einmalig: Altbestand der Platte verschlüsselt nach `KAI-VAULT\legacy\` (erledigt 25.09.2026) |
| `restore_test.ps1` | `.local\bin\` | Wöchentlicher Restore-Test der DPAPI-Kopien auf C: (inkl. `integrity_check`) |
| `sync-memory.ps1` | `KAI-mirror\` | Spiegel des Claude-Memory (beide Projektverzeichnisse) |
| `mirror_backups_offsite.ps1` | `.local\bin\` | Lokale Zweitkopie der Chiffrate auf C: — **nicht** offsite (OneDrive synchronisiert nicht) |
| `tasks\KAI-Vault-OnAttach.xml` | `KAI-mirror\scripts\tasks\` | Task: Vault-Lauf beim Anstecken der Platte + tägliche Erinnerung |

Schlüssel liegen nie hier und nie auf der Platte: `KAI_BACKUP_PASSPHRASE` kommt aus der Pi-`.env` (Rückfall:
DPAPI-.env-Sicherung), `LN_SECRET_BACKUP_KEY` aus einer DPAPI-Arbeitskopie (`kai_vault.ps1 -StoreLnKey`); beide
Originale liegen in KeePass. Nicht hier versioniert: die Wrapper mit `Wait-KaiNetwork` (bin-bd) und
`sync-scb-from-node.ps1` (Lightning-Lane) — sie folgen in Absprache mit ihren Besitzern.
