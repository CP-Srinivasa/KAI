# Runbook — Premium-Signal-Pipeline Hardening (2026-06-02)

Operationalisiert die Forensik zu env `ENV-TG-001275462917-23879-502ef70a`
(US/USDT, LONG, 10x). **Bewiesener Lifecycle:** Envelope `accepted` →
Auto-Fill-Approval `ok` → approved-Source erlaubt → Risk-Reject
`max_open_positions_reached:6>=6`. Kein Paper-/Live-Fill. Telegram/Parser/TTL/
Exchange waren **nicht** der finale Blocker — das volle Buch war es.

## Was dieser Sprint liefert

| Paket | Modul | Default | Wirkung |
|---|---|---|---|
| Reason-Codes | `app/risk/reason_codes.py` | aktiv | Stabile `REJECT_*`-Vocabulary + `FinalStatus`-Enum (EXECUTED/REJECTED_WITH_REASON/EXPIRED/QUARANTINED/FAILED_RETRYABLE/FAILED_FINAL/UNKNOWN_REQUIRES_RECONCILIATION). |
| Risk-Gates (Gate 10) | `app/risk/engine.py` | **OFF** | min_rr, min_avg_rr, max_signal_risk_pct, max_leveraged_risk_pct, min_net_edge_bps, min_target_distance_pct. Fail-closed bei aktiviertem Gate + fehlender Geometrie. Diagnostics IMMER geloggt. |
| Exchange-Preflight | `app/execution/exchange_preflight.py` | aktiv (Modul) | Tick/Step/Notional/Percent-Price/Leverage. Pflicht-Gate vor jedem künftigen Live-Order-Send. |
| Parser-Hardening | `app/ingestion/telegram_channel_parser.py` | aktiv | `·`/`•`/`|`-Separatoren, `SL:`-Kurzform, bare `10x`-Leverage. |
| Quarantäne | `app/ingestion/parser_quarantine.py` | opt-in | Raw-Store vor Parse + `parser_quarantine.jsonl` + Operator-Alert statt Silent Drop. |
| Session-Lock | `app/ingestion/telegram_session_lock.py` | opt-in | Host+PID-Lock gegen AuthKeyDuplicated-Dual-Host. |
| Capacity-Report | `app/observability/capacity_report.py` | read-only | De-Stau-Sicht; markiert stale Pendings, löscht nie. |

## Risk-Gates aktivieren — STAGED (off → audit → enforce)

Die Gates sind default-OFF; der Modus ist `RISK_GATES_MODE` (default **audit**).
Reihenfolge — **niemals direkt enforce**:

**Schritt 1 — audit (beobachten, nicht blocken):**
```
RISK_GATES_MODE=audit
RISK_MAX_LEVERAGED_RISK_PCT=35.0   # Nenner = stop_distance_pct*leverage, NICHT equity!
RISK_MIN_RR=0.5
RISK_MIN_TARGET_DISTANCE_PCT=0.3
```
`systemctl restart kai-server`, dann ≥1 Tag laufen lassen. Auswerten:
```bash
python -m app.observability.risk_gate_audit   # reject-rate, code-distribution, by-symbol/source
```
Prüfen: Fängt es das US/USDT-Signal? Welche Codes dominieren? Wie viele
*legitime* Signale würden fälschlich blockiert? Wäre das Buch zu oft leer?

**Schritt 2 — enforce (erst wenn audit plausibel, nur paper):**
```
RISK_GATES_MODE=enforce
```
Dann trägt ein abgelehntes Signal `reason_codes:["REJECT_RISK_TOO_HIGH", …]` +
`signal_geometry` im `bridge_pending_orders.jsonl`-Reject-Record; in `audit`
steht dasselbe als `would_reject` in `artifacts/risk_gate_audit.jsonl`.

**Wichtig:** Diese Gates sind Pipeline-Härtung, **keine** Edge-/Strategie-
Reparatur. `EXECUTION_ENTRY_MODE` bleibt unverändert; sie sind kein Grund, den
Entry-Loop zu reaktivieren.

## Capacity / Buch-Entstauung

**Diagnose (read-only):**
```bash
python -m app.observability.capacity_report --max-open-positions 6 --ttl-hours 24
# oder --json
```
Zeigt offene Positionen (`open_count/max`), offene Symbole, Pending-Orders und
**stale Pendings** (älter als TTL = Archiv-Kandidaten).

**Entstau-Regeln (hart):**
- Buch voll → entweder `RISK_MAX_OPEN_POSITIONS` anheben **oder** eine Position
  regulär schließen/auslaufen lassen.
- **Niemals** eine offene Live-Position löschen.
- Pending-Order nur archivieren, wenn **beweisbar** final/expired (stale > TTL
  UND kein späterer Fill/Close in `bridge_pending_orders.jsonl`).
- `artifacts/*.jsonl` niemals truncaten solange `*.jsonl.lock` aktiv.

## Smoke / Verifikation nach Deploy

```bash
python -m pytest tests/unit/test_risk_reward_gates.py \
  tests/unit/test_exchange_preflight.py \
  tests/unit/test_parser_hardening_and_quarantine.py \
  tests/unit/test_session_lock_and_capacity.py -q
systemctl list-units --state=failed,inactive | grep kai- || echo "alle kai-units ok"
```

## Rollback

Alle Code-Gates sind default-OFF bzw. additiv. Rollback = Gate-Env-Vars
entfernen (Risk-Gates) bzw. Branch revert. Keine Schema-Migration, keine
Daten-Mutation. Der Parser-Fix ist reine Erweiterung (212 Bestands-Tests grün).

## Bekannte offene Punkte / TODO

- Preflight ist gebaut + getestet, aber es existiert **kein** Live-Order-Send-
  Pfad (Paper-only). Bei Live-Wiring MUSS jeder Send durch `preflight_order`.
- `parse_or_quarantine` / `store_raw` / `session_lock` sind standalone; der
  Telethon-Worker (parallele Codex-Entwicklung) muss sie am Ingestion-Punkt
  bzw. Startup adoptieren.
- trading_loop `check_order` threadet leverage/targets noch nicht (Gates dort
  default-OFF → kein Effekt; Bridge-Pfad — der relevante — ist verdrahtet).
