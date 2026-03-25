# RISK_POLICY.md

## Ziel

Kapitalerhalt vor Ertrag. Die Risk Engine ist ein harter Vorfilter und darf nicht umgangen werden.

## Konservative Baseline

- `max_risk_per_trade_pct <= 0.25`
- `max_daily_loss_pct <= 1.0`
- `max_total_drawdown_pct <= 5.0`
- `max_open_positions <= 3`
- `max_leverage = 1.0`
- `min_signal_confidence >= 0.75`
- `min_signal_confluence_count >= 2`
- `require_stop_loss = true`
- `allow_averaging_down = false`
- `allow_martingale = false`
- `kill_switch_enabled = true`

## Harte Regeln

- keine Order ohne Risk Check
- keine Handlung ohne Invalidation bzw. Stop-Loss-Regel
- keine Ausführung bei Unsicherheit, Datenstaleness oder aktivem Kill Switch
- keine Live-Handlung ohne explizite Freigabe

## Referenzen

- Settings: `app/core/settings.py`
- Limits / Result Models: `app/risk/models.py`
- Enforcement: `app/risk/engine.py`
- Paper Execution: `app/execution/paper_engine.py`
