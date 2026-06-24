# L3 OTS Cutover — Runbook (stamper=null → opentimestamps)

**Zweck:** Den täglichen Audit-Anchor von `stamper=null` (nur Digest aufgezeichnet,
**keine echten Proofs**) auf **echte OpenTimestamps-Proofs** umstellen und den
asynchronen Upgrade-Pass aktivieren, der pending Proofs zu Bitcoin-verankerten
Proofs macht.

**Doktrin-Einordnung:** Reine **Daten-Integritäts-Schicht** (L3). Unabhängig von
den LN-Kapital-Gates (G1/G2). Es verlässt **nur ein SHA256-Hash** des Audit-Digests
die Node Richtung öffentlicher OTS-Calendars + Bitcoin — **kein Audit-Inhalt, kein
Kapital**. Calendars sind öffentlich/gratis (`alice`/`bob.btc.calendar.opentimestamps.org`).
Dies ist der **externe** Schritt → Operator-ausgeführt.

**Voraussetzung:** PR (`claude/p7/l3-ots-real`) gemergt **und** auf Pi deployed
(`git pull` der Mainline auf `/home/kai/ai_analyst_trading_bot`).

---

## 1. opentimestamps-Lib im Pi-venv sicherstellen (Pull allein reicht NICHT)

`opentimestamps` ist eine harte Dependency (pyproject), wird aber **lazy** importiert
— ein alter venv hat sie evtl. noch nicht. Nach jeder pyproject-Dep-Änderung gilt:
`pip install -e .` (Lehre: pull ≠ install).

```bash
cd /home/kai/ai_analyst_trading_bot
.venv/bin/python -m pip install -e .
.venv/bin/python -c "import opentimestamps; print('ots ok', opentimestamps.__version__)"
```

## 2. `.env` auf der Pi setzen (`/home/kai/ai_analyst_trading_bot/.env`)

```dotenv
APP_INTEGRITY_ENABLED=true
APP_INTEGRITY_STAMPER=opentimestamps
APP_INTEGRITY_AUDIT_PATHS=["artifacts/paper_execution_audit.jsonl"]
```

- `APP_INTEGRITY_AUDIT_PATHS` ist eine **JSON-Liste** (pydantic-settings parst
  komplexe Typen als JSON). Die Replay-SSOT (`paper_execution_audit.jsonl`) ist der
  Pflicht-Eintrag; weitere Audit-Streams können ergänzt werden, z. B.
  `["artifacts/paper_execution_audit.jsonl","artifacts/decisions.jsonl"]`.
- **Keine Inline-Kommentare** in der `.env` (systemd EnvironmentFile crasht daran).
- `APP_INTEGRITY_PROOFS_DIR` bleibt Default `monitor/integrity` (oder setzen).

## 3. Upgrade-Timer installieren + aktivieren

Der Anchor-Timer (`kai-integrity-anchor.timer`, täglich 04:20 UTC) existiert bereits.
NEU der Upgrade-Timer (alle 6 h), der pending → Bitcoin-confirmed nachzieht:

```bash
sudo cp deploy/systemd/kai-integrity-ots-upgrade.service /etc/systemd/system/
sudo cp deploy/systemd/kai-integrity-ots-upgrade.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kai-integrity-ots-upgrade.timer
systemctl list-timers 'kai-integrity*'   # beide Timer sichtbar?
```

## 4. kai-server neu starten (Dashboard liest Settings gecacht)

```bash
sudo -n systemctl restart kai-server
```

---

## Verifikation (ehrliche Evidenz, nicht „läuft schon")

1. **Anchor erzeugt jetzt einen echten Proof** (nicht mehr „recorded … stamper=null"):
   ```bash
   .venv/bin/python scripts/integrity_anchor_audit.py
   # erwartet: "integrity-anchor: anchored digest=…  proof=…/audit-….ots"
   ls -l monitor/integrity/audit-*.ots    # .ots-Datei vorhanden
   ```
2. **Proof ist zunächst PENDING** (Calendar-Commitment, noch nicht Bitcoin-gemined):
   ```bash
   curl -s localhost:8000/dashboard/api/integrity | python -m json.tool | grep -E 'proof_state|proof_available|bitcoin_height'
   # erwartet: proof_available=true, proof_state="pending", bitcoin_height=null
   ```
   Dashboard-KPI „Audit-Integrität (L3)" zeigt **„OTS pending · wartet auf Bitcoin-Bestätigung"**.
3. **Nach Mining (Stunden bis ~1 Tag) upgraden** — der Timer macht das automatisch
   alle 6 h; manuell anstoßen zum Prüfen:
   ```bash
   .venv/bin/python scripts/integrity_ots_upgrade.py
   # erwartet (sobald gemined): "scanned=N upgraded≥1 …"; vorher "still_pending=N"
   curl -s localhost:8000/dashboard/api/integrity | grep -E 'proof_state|bitcoin_height'
   # nach Upgrade: proof_state="confirmed", bitcoin_height=<Blockhöhe>
   ```
   Dashboard-KPI zeigt dann **„Bitcoin-verankert · Bitcoin #<height>"**.
4. **Post-Deploy-Smoke:** `systemctl --failed` leer; `journalctl -u kai-integrity-ots-upgrade.service -n 20` ohne Fehler.

## Rollback (harmlos, kein Kapital)

```bash
# in /home/kai/.env:
APP_INTEGRITY_STAMPER=null        # oder APP_INTEGRITY_ENABLED=false
sudo systemctl disable --now kai-integrity-ots-upgrade.timer
sudo -n systemctl restart kai-server
```
Bereits geschriebene `.ots`-Proofs bleiben gültig; es werden nur keine neuen mehr erzeugt.

## Hinweise / Fallen

- **Erst Anchor, dann Upgrade:** Ein frischer Proof ist immer erst pending; der
  Upgrade-Pass kann ihn erst confirmen, wenn die Calendar-Aggregation in einem
  Bitcoin-Block ist (Stunden). `still_pending` ist also normal/erwartet.
- **Fail-soft:** Calendar down / noch nicht gemined → Proof bleibt pending, kein
  Fehler. Fehlende Lib → Runner exit 1 mit klarer Meldung (→ Schritt 1).
- **Nur Hash verlässt die Node** — Audit-Inhalte nie. Datenschutz-/Souveränitäts-konform.
