# Runbook — Lightning Ops-Ledger v2 Migration

Ziel: das historische `artifacts/ln_ops_ledger.jsonl` ohne Preimages, rohe
BOLT11-Rechnungen, Empfänger oder Route-Hops in das hash-verkettete v2-Schema
überführen. Das Skript verändert oder löscht die Quelle nie und berührt LND nicht.

1. `kai-server` und alle anderen Writer stoppen; exakten Quellpfad prüfen.
2. Migration in eine neue Datei ausführen:

       python scripts/redact_ln_ops_ledger.py \
         artifacts/ln_ops_ledger.jsonl artifacts/ln_ops_ledger.v2.jsonl

3. Im Report müssen `verification.ok=true` und `open_intents=[]` gelten. Danach
   beide SHA-256-Werte sowie die Truth-Attestation sichern.
4. Die alte Datei verschlüsselt/off-box archivieren, Zugriffsrechte prüfen und erst
   dann die verifizierte v2-Datei als `artifacts/ln_ops_ledger.jsonl` einsetzen.
   Das Ersetzen/Löschen ist eine separate Operator-Aktion und nicht Teil des Skripts.
5. Server starten; `/dashboard/api/ln/ops` muss ohne Auth `401/403` liefern. Einen
   Dry-run planen und anschließend `verify_ln_ops_ledger()` erneut prüfen.

Rollback: bei jedem Zweifel die Writer gestoppt lassen und die Dateiumstellung
zurücknehmen. Niemals eine teilweise geschriebene oder unverifizierte Datei einsetzen.
