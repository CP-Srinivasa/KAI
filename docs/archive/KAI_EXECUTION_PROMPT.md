# KAI_EXECUTION_PROMPT.md
# Execution-Prompt: Signal → Entscheidung → Ausführung
# Version: v1 — 2026-03-21 — Rebaseline-Stand Sprint 36

---

## Zweck dieses Prompts

Dieser Prompt ist aktiv wenn KAI einen Signal-Zyklus, eine Backtest-Sequenz
oder eine Entscheidungsprotokollierung durchführt. Er definiert den verbindlichen
Ausführungspfad und alle Sicherheitsgates.

---

## Kanonischer Ausführungspfad

```
AnalysisResult
    │
    ▼
SignalGenerator.generate()         ← 6 Filter-Gates (confidence, actionable,
    │                                  sentiment, confluence, price, stale)
    ▼
SignalCandidate (frozen, decision_id)
    │
    ▼
RiskEngine.check_order()           ← 8 Pre-Order-Gates (Kill-Switch, daily_loss,
    │                                  drawdown, position_limit, confidence,
    ▼                                  confluence, stop_loss, leverage)
RiskEngine.calculate_position_size()
    │
    ▼
PaperExecutionEngine.create_order()  ← idempotency_key Dedup
PaperExecutionEngine.fill_order()    ← slippage + fee simulation
    │
    ▼
RiskEngine.update_daily_loss()
    │
    ▼
JSONL Audit (append-only)
```

---

## Nicht verhandelbare Gates

### Gate 1 — Kill Switch
```
if risk_engine.kill_switch_active:
    → REJECT ALL ORDERS
    → status = "kill_switch_halted"
    → AUDIT and RETURN
```

### Gate 2 — Daily Loss
```
if realized_loss_pct >= max_daily_loss_pct:
    → REJECT
```

### Gate 3 — Total Drawdown
```
if total_drawdown_pct >= max_total_drawdown_pct:
    → REJECT
```

### Gate 4 — Position Limit
```
if open_positions >= max_open_positions:
    → REJECT
```

### Gate 5 — Signal Confidence
```
if signal.confidence_score < min_signal_confidence:
    → REJECT
```

### Gate 6 — Confluence
```
if signal.confluence_count < min_signal_confluence_count:
    → REJECT
```

### Gate 7 — Stop Loss Required
```
if require_stop_loss and signal.stop_loss_price is None:
    → REJECT
```

### Gate 8 — Live Execution Locked
```
if live_enabled is not explicitly True in settings:
    → Paper only, ALWAYS
```

---

## Execution-Invarianten

- `execution_enabled = False` auf allen Outputs
- `write_back_allowed = False` auf allen Summaries
- Kein Trade ohne positives `RiskCheckResult.approved`
- Kein Trade ohne gültigen `stop_loss_price` (wenn `require_stop_loss=True`)
- Kein Trade bei aktivem Kill Switch
- Alle Fills sind paper-simuliert (slippage + fee)
- Jeder Zyklus wird in JSONL-Audit geschrieben (auch No-Signal und Risk-Rejected)
- `DecisionInstance`-Einträge haben keinen Execution-Seiteneffekt

---

## Konservative Risiko-Baseline (Default-Settings)

| Parameter                  | Default | Bedeutung                              |
|----------------------------|---------|----------------------------------------|
| `max_risk_per_trade_pct`   | 0.25    | Max 0.25% des Kapitals pro Trade       |
| `max_daily_loss_pct`       | 1.0     | Tageslimit 1% Verlust                  |
| `max_total_drawdown_pct`   | 5.0     | Gesamtdrawdown-Limit 5%                |
| `max_open_positions`       | 3       | Max 3 offene Positionen                |
| `max_leverage`             | 1.0     | Kein Leverage (1x)                     |
| `min_signal_confidence`    | 0.75    | Min 75% Konfidenz                      |
| `min_signal_confluence_count`| 2     | Min 2 Confluence-Faktoren              |
| `require_stop_loss`        | True    | Stop-Loss immer erforderlich           |
| `allow_averaging_down`     | False   | Verboten                               |
| `allow_martingale`         | False   | Verboten                               |
| `kill_switch_enabled`      | True    | Kill Switch immer aktiv                |

---

## Backtest-spezifische Regeln

- `BacktestEngine` verwendet immer `PaperExecutionEngine(live_enabled=False)`
- `max_leverage=1.0` — hardcoded, nicht konfigurierbar
- `direction_hint="neutral"` → `outcome="skipped_neutral"` (immer)
- `direction_hint="bearish"` + `long_only=True` → `outcome="skipped_bearish"`
- Kill Switch Aktivierung → alle nachfolgenden Signale: `kill_switch_halted`
- Marktpreise werden extern übergeben — kein interner Datenfetch in `run()`
- Jeder `run()`-Aufruf: ein Audit-Eintrag in `artifacts/backtest_audit.jsonl`

---

## Entscheidungsprotokoll (Decision Journal)

- `DecisionInstance` hat 26 Pflichtfelder (schema-validiert)
- `decision_id` ist deterministisch (SHA256 aus symbol+mode+timestamp+thesis)
- JSONL append-only — keine Mutation nach dem Schreiben
- `DecisionJournalSummary.execution_enabled` ist immer `False`
- Kein Journal-Eintrag triggert einen Trade oder eine Statusänderung

---

## Telegram-Execution-Regeln

- `/kill` ist confirm-gated — keine versehentliche Aktivierung
- `/approve` und `/reject` sind aktuell audit-only ohne Live-Seiteneffekt
- `/pause` und `/resume` sind nur außerhalb von `dry_run=True` aktiv
- Alle Kommandos werden audit-geloggt in `artifacts/operator_commands.jsonl`
