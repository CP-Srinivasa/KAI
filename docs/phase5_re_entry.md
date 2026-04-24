# PHASE 5 Re-Entry Playbook

**Gilt ab 2026-05-16 (D-125 Kalender-Half).**

Nach 30 Tagen TV-Pivot-Pause wird PHASE 5 (Quality-Bar-Tuning) reaktiviert — wenn die Datenbasis signifikant größer geworden ist und die Active-Precision-CI eng genug für belastbare Threshold-Optimierung ist.

## Gate-Kriterium (hart)

PHASE 5 nur reaktivieren wenn **beides** gilt am 2026-05-16:

1. **Data-side**: `resolved_directional_documents ≥ 200` **oder** `order_filled_count_with_pnl ≥ 10`
2. **Quality-side**: Active-Precision (ex `unknown`-Source) CI-Width ≤ 20pp bei n ≥ 100

Stand 2026-04-24: Data-side bereits erfüllt (305 / 54). Quality-side zum Stichtag via `alerts tv4-quality-bar` neu berechnen.

## Messung (fixe Runs am Stichtag)

```bash
python -m app.cli.main alerts tv4-quality-bar --output-path artifacts/ph5_hold/quality_bar_20260516.json
python -m app.cli.main alerts hold-report --out artifacts/ph5_hold/hold_metrics_20260516.json
python scripts/ph5_hold_metrics_report.py
```

**Abnahme-Artefakte**:
- `artifacts/ph5_hold/quality_bar_20260516.json` — per-Source Precision + Wilson-95-CI
- `artifacts/ph5_hold/hold_metrics_20260516.json` — Forward-Precision mit Priority-Gate
- `artifacts/daily_strategy/2026-05-16.md` — Operator-Review mit Gate-Entscheidung

## Entscheidungs-Matrix

| Quality-side | Data-side | Entscheidung |
|---|---|---|
| ✅ (CI ≤ 20pp, n ≥ 100) | ✅ (≥200 resolved **oder** ≥10 fills) | **PHASE 5 Re-Entry** — Threshold-Tuning + Technische Indikatoren entblockt |
| ✅ | ❌ | **Weiter warten**, +30 Tage, Pipeline operativ halten |
| ❌ (CI > 20pp) | ✅ | **Weiter warten**, mehr Signal-Volumen → CI verkleinern |
| ❌ | ❌ | **Zurück zu Datenqualität** — Feeds / Keywords / Spam-Filter prüfen, kein Tuning |

## Was Re-Entry entblockt

**Freigegeben nach Pass**:
- Threshold-Tuning auf Priority-Tier-Gate (`EXECUTION_PAPER_MIN_PRIORITY`)
- Bearish-Confidence + Impact + Regime-Thresholds (D-121-Komplex)
- Technische Indikatoren (RSI/MACD/Hash-Ribbons) als Bestätigung
- God-Class-Splits (`TelegramOperatorBot`, `cli/main.py`, `hold_metrics.py`)
- Binary-Entscheidungen zu Zombie-Subsystemen (Signal-Consensus, MCP-Server, `tradingview_consumer.py`)
- Multi-Agent-Re-Eval (D-186 Follow-up)

**Bleibt blockiert (unverändert)**:
- Live-Trading-Execution (`mode=live` nicht aktivieren)
- Companion-ML Reaktivierung (D-107 permanent out-of-scope)

## Entscheidungs-Flow (operativ)

1. **Morgens 2026-05-16**: Stichtag-Messungen ausführen (Commands oben)
2. **Daily-Strategy-Review erstellen**: volles 6-Sektionen-Format mit Gate-Entscheidung
3. **DECISION_LOG-Eintrag**: `D-<N> PHASE 5 Re-Entry (Pass/Fail)` mit Zahlen, CI, Begründung
4. **MEMORY.md aktualisieren**: `project_tv_pivot.md` auf "closed" umstellen, neue Roadmap-Phase verankern
5. **Bei Pass**: erster kleiner Sprint auf Threshold-Tuning oder Indikatoren — nicht beides gleichzeitig

## Rollback-Pfad

Wenn Re-Entry entschieden, aber erster Tuning-Sprint zeigt **regression**:
- Rollback via git revert des Tuning-Commits
- DECISION_LOG-Eintrag `D-<N+1> PHASE 5 Re-Entry Rollback`
- Tuning pausieren, Datenbasis weitere 14 Tage wachsen lassen, erneut prüfen

## Nicht-verhandelbare Bedingungen (aus D-125, gelten fort)

1. Live-Trading bleibt OFF
2. Approval-Mode bleibt Pflicht
3. Fail-closed bleibt fail-closed
4. Provenance-Persistenz bleibt Pflicht (source + version + signal_path_id + auth_method + ingest_event_id + provenance_hash)
5. Kein weiterer Aufschub über 2026-05-16 hinaus — bei Fail entweder tuning-loop oder data-quality-loop, nicht "nochmal 30 Tage warten"

## Verweise

- `DECISION_LOG.md` D-125 (Pivot-Start), D-179/D-183 (Security-V8), D-186 (Multi-Agent-Gate)
- `artifacts/ph5_hold/` (Messung-Artefakte)
- `memory/project_tv_pivot.md` (TV-Pivot-Stufenplan TV-1..TV-4b)
- `memory/project_provenance_persistence.md` (V1-Stand)
