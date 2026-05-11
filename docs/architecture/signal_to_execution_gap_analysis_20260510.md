# Signal-to-Execution Pipeline — Gap-Analyse 2026-05-10

**Stand:** 2026-05-10 · **Branch:** `claude/signal-pipeline-gap-analysis-20260510` · **Baseline:** `76e3de5` (claude/p7/reentry-ia-codex-cycle) · **Pi-5 Live verifiziert**

## TL;DR

Die Premium-Telegram-Signal-Pipeline ist zu **~80% gebaut**. Sie zerbricht an konkreten, lokalisierbaren Stellen — kein Re-Build nötig, gezielter Patch + Lifecycle-Layer.

**Drei dominante Defekte:**

1. **SHORT-Signale werden komplett abgelehnt** (`envelope_to_paper_bridge.py:504-510`)
2. **Leverage + Margin werden ignoriert** — channel-stated `Leverage: 10x` / `Margin: 5%` haben keinen Effekt
3. **Operator-Approval-Pflicht** für Premium-Channel ist scharf, viele Signale werden als `skipped_source` verworfen weil Operator nicht klickt

Plus strukturelle Lücken: keine Type-Hierarchie ParsedSignal → ExecutableOrderIntent, keine Lifecycle-State-Machine im Operator-verlangten Format, kein deterministischer Entry-Watcher (nur Tick-basiert), keine Crash-Recovery, keine durchgängige correlation_id über alle Audit-Streams.

## Antworten auf die 10 Leitfragen aus dem Operator-Auftrag

| # | Frage | Antwort |
|---|---|---|
| 1 | Wo wird das Signal korrekt erkannt? | `app/ingestion/telegram_channel_parser.py:292 parse_premium_channel_message()` — robuster Regex-Parser, ParsedSignal-Dataclass mit symbol/side/entry_type/entry_min/max/SL/targets/leverage |
| 2 | Wo wird es in ein Portfolio-Objekt geschrieben? | **Nirgends direkt.** Envelope landet in `artifacts/telegram_message_envelope.jsonl`. Erst nach Operator-Click (Approval-Mode) + Bridge-Gates → Paper-Engine-Fill wird es zu `PaperPosition` |
| 3 | Wo geht Information verloren? | (a) **Leverage** — Bridge-Docstring: *"Channel-stated leverage is ignored in paper mode"*. (b) **Margin** — ParsedSignal hat keine margin-Felder. (c) **Targets 2-N** — V25-C lädt sie, aber Bridge nutzt nur `tp1 = targets[0]` |
| 4 | Trennung Portfolio-Anzeige vs ausführbare Order? | **Existiert** als Stage-Audit in `bridge_pending_orders.jsonl`, aber **nicht in ParsedSignal selbst**. Kein Status-Feld auf dem Datenmodell |
| 5 | Wird Signal nur gespeichert, aber nie executed? | **Ja — strukturell.** Bridge-Allowlist-Default = `dashboard,telegram_premium_channel_approved`. Premium-Channel ohne Approval-Click → `skipped_source` |
| 6 | Entry-Watchlist? | **Implizit über `bridge.run_tick()`** — pending mit `reason=price_outside_tolerance` re-checkt bei jedem Tick. Kein expliziter Watcher, kein deterministisches Trigger |
| 7 | Wird Entry-Preis live überwacht? | **Tick-basiert.** Wenn Markt 10s in der Range ist und der Tick danach kommt, **Signal verpasst** |
| 8 | Status WAITING_FOR_ENTRY? | **Stage `pending` mit reason** ist Äquivalent, aber **nicht** im Operator-verlangten Schema. Keine Enum, keine Transition-Validierung |
| 9 | Timeouts/Expiry-Regeln? | **Ja:** `operator_signal_ttl_hours=24` (Default). Bridge audit `expired` wenn überschritten |
| 10 | Audit-Logs für „nicht ausgeführt, weil…"? | **Ja, sehr gut:** `bridge_pending_orders.jsonl` hat klare Stages. **Lücke:** kein Display im Telegram/Dashboard für den Operator |

## Datenfluss IST

```
Premium-Telegram-Channel
  ↓ MTProto (telethon)
telegram_channel_worker.py — async on_new_message
  ↓ parse_premium_channel_message(text)
ParsedSignal (frozen dataclass — KEIN status, KEIN margin)
  ↓ emit_parsed_signal(parsed)
artifacts/telegram_message_envelope.jsonl (source="telegram_premium_channel")
  ↓ telegram_channel_approval.send_approval_request() — Operator [✅Fill]/[❌Ignore]
  ↓ Operator klickt [✅Fill]
  ↓ handle_signal_approval() → re-emit mit source="telegram_premium_channel_approved"
  ↓ Bridge-Scheduler tick (EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED=true auf Pi 5)
envelope_to_paper_bridge.run_tick()
  ↓ Gate 1: allowlist (telegram_premium_channel_approved ✓)
  ↓ Gate 2: TTL (24h)
  ↓ Gate 3: completeness (entry/SL/TP/direction/side)
  ↓ Gate 3.1: SHORT REJECT  ❌ HARTE LÜCKE
  ↓ Gate 3.5: existing_position REJECT (no-merge)
  ↓ Gate 4: market-data + scale-detection (V25-D)
  ↓ Gate 5: tolerance ±0.5% — sonst pending(price_outside_tolerance)
PaperExecutionEngine.create_order(symbol, side="buy", entry, SL, TP1)
  ↓ Risk-Engine sizing via max_risk_per_trade_pct (LEVERAGE IGNORIERT  ❌)
  ↓ idempotency_key check
PaperFill written → PaperPosition opened
artifacts/paper_execution_audit.jsonl
```

## Datenfluss SOLL

```
... (bis ParsedSignal unverändert)
ParsedSignal
  ↓ NormalizedTradeSignal: + correlation_id, status=PARSED, margin_mode, risk_allocation
  ↓ SignalValidator: Pflicht-Felder, → status=VALIDATED|REJECTED_INVALID_SIGNAL+audit_reason
  ↓ ExecutableOrderIntent: einheitlicher Vertrag für Paper + Live
  ↓ EntryRangeWatcher: deterministisch market-data-getriggert,
                       status=WAITING_FOR_ENTRY → ENTRY_TRIGGERED
PaperEngine + LiveEngine: beide akzeptieren ExecutableOrderIntent
  ↓ Lifecycle-Transitions (15-State-Enum + Transition-Matrix)
  ↓ ORDER_BUILDING → ORDER_SUBMITTED → ORDER_ACCEPTED → POSITION_OPEN
  ↓ Crash-Recovery: WAITING_FOR_ENTRY/ORDER_SUBMITTED rehydrieren beim Restart
AuditStream: single kai_audit_service mit correlation_id-Kette
```

## Lifecycle-State-Machine (15 States)

```
RECEIVED → PARSED → VALIDATED → WAITING_FOR_ENTRY → ENTRY_TRIGGERED →
  ORDER_BUILDING → ORDER_SUBMITTED → ORDER_ACCEPTED → POSITION_OPEN →
  (PARTIAL_TP_HIT*)+ → TP_HIT|SL_HIT
                                    ↘ CANCELLED|EXPIRED|FAILED|REJECTED_INVALID_SIGNAL
```

Jede Transition braucht: `validated_by transition_matrix`, `timestamp_utc`, `reason: str`, `actor: "EntryWatcher"|"PaperEngine"|"Operator"|"RiskEngine"`.

## Type-Hierarchie

```
A) RawTelegramMessage      — Telethon-Message-Object (text + metadata)
B) ParsedSignal            — bestehend, app/ingestion/telegram_channel_parser.py:58
C) NormalizedTradeSignal   — NEU: Status, correlation_id, margin, risk_allocation
D) ExecutableOrderIntent   — NEU: einheitlicher Paper/Live-Vertrag mit idempotency_key
E) PaperOrder/LiveOrder    — bestehend, je Engine spezifisch
```

## Betroffene Dateien

**Bestehend, müssen erweitert werden:**

- `app/ingestion/telegram_channel_parser.py` (+margin-Extraktor)
- `app/ingestion/telegram_channel_envelope.py` (+correlation_id propagation)
- `app/execution/envelope_to_paper_bridge.py` (SHORT-Pfad, Leverage-Honoring, Status-Layer)
- `app/execution/paper_engine.py` (open-short primitive, Lifecycle-Transitions — Pre-Sprint A)
- `app/execution/models.py` (OrderLifecycleState Enum + Transition-Matrix)
- `app/execution/exchanges/binance.py` + `bybit.py` (Live-Adapter ExecutableOrderIntent — Pre-Sprint C)
- `app/risk/engine.py` (Margin-Mode-Berücksichtigung)
- `app/core/settings.py` (Felder: `honor_channel_leverage`, `default_margin_mode`)

**Neu zu schaffen:**

- `app/execution/normalized_signal.py` — NormalizedTradeSignal + SignalStatus + Validator
- `app/execution/order_intent.py` — ExecutableOrderIntent (Pflicht-Vertrag)
- `app/execution/entry_watcher.py` — deterministischer EntryRangeWatcher
- `app/execution/lifecycle.py` — State-Machine + Transition-Validator
- `app/execution/recovery.py` — Crash-Recovery

## Sicherheits- und Risikoentscheidungen

1. **SHORT-Pfad** ist nicht trivial — Paper-Engine hat keine `open_short_position()`-Primitive. Eigener Sub-Sprint (~1 Tag). Bis dahin: Bridge-Reject mit klarem Operator-Hinweis statt stillem Reject.
2. **Leverage-Honoring** muss sich auf **Notional** beziehen, nicht Margin. `app/security/live_caps.py` ist korrekt: `notional_usd > MAX_POSITION_USD`. Paper muss Notional ebenfalls sichtbar machen.
3. **Approval-Mode bleibt Pflicht** — kein Auto-Fill aus Premium-Channel ohne Operator-Click. Bei `LIVE_TRADING` zusätzlich HOTP pro Trade.
4. **Idempotency-Key durchgängig** — `correlation_id` ist Wahrheit; Bridge + Paper + Live müssen sie respektieren, sonst Crash-Recovery → Doppel-Fill möglich.
5. **Plausibility-Check Entry vs SL** — wenn LONG mit SL > Entry oder SHORT mit SL < Entry: hartes Reject. Heute: Validator prüft das nicht explizit.

## Noch offene Risiken

| # | Risiko | Mitigation |
|---|---|---|
| R1 | SHORT-Implementierung in Paper-Engine könnte unbemerkt Verhaltensbruch in V-DB5/Adaptive-Learning auslösen | feature-flag `paper_engine_shorts_enabled`, default off, Tests rückwirkend |
| R2 | Leverage-Honoring lässt Paper-Daten von Live-Daten abweichen | Pre-Sprint C Parity-Vertrag erzwingt identisches Sizing-Calc |
| R3 | EntryRangeWatcher als eigener Loop kann Race-Conditions mit Bridge-Tick erzeugen | Single-Owner-Pattern: entweder Bridge ODER Watcher, nicht beide |
| R4 | Operator-UX-Lücke ("warum wurde X nicht ausgeführt") braucht Telegram-Renderer-Erweiterung | Aufgabenpaket 7 (AuditStream) macht es nutzbar |

## Implementierungs-Reihenfolge

| # | Sprint | Aufwand | Was es liefert | Blocker |
|---|---|---|---|---|
| 1 | **AP-3+5: NormalizedTradeSignal + Validator** | 1 Tag | Type-Hierarchie, Margin/Leverage als Pflicht-Felder, Validator mit Audit-Reason | — |
| 2 | **Pre-Sprint A: Lifecycle-State-Machine** | 6h | 15-State-Enum + Transition-Matrix in `paper_engine` | AP-3+5 sinnvoll vorher |
| 3 | **AP-6 + Pre-Sprint C: ExecutableOrderIntent + Parity** | 1 Tag | Einheitlicher Vertrag Paper/Live | Pre-Sprint A |
| 4 | **AP-2 SHORT-Pfad in Paper-Engine** | 1 Tag | `open_short_position()` Primitive + Tests | Pre-Sprint A |
| 5 | **Leverage-Honoring + Margin-Felder** | 4h | Channel-Leverage wirkt, Notional-Cap statt Margin-Cap | AP-3+5 |
| 6 | **AP-4: EntryRangeWatcher** | 1 Tag | Deterministischer Watcher statt nur Tick-Polling | AP-3+5 |
| 7 | **Pre-Sprint D: AuditStream-Konsolidierung** | 6-8h | `kai_audit_service` als Single-Writer mit correlation_id | — |
| 8 | **Pre-Sprint B: Crash-Recovery** | 4-6h | WAITING_FOR_ENTRY/ORDER_SUBMITTED-Recovery | Lifecycle + AuditStream |
| 9 | **AP-9: 15 Test-Cases** | 1 Tag | Akzeptanzkriterium-Tests | alles davor |

**Total: 8-10 Arbeitstage Solo, parallelisierbar 5-6 Tage.**

## Live-Verifikation auf Pi 5

```bash
# Bridge-Stage-Histogramm
ssh ubuntu@192.168.178.23 "tail -100 /home/ubuntu/ai_analyst_trading_bot/artifacts/bridge_pending_orders.jsonl | python3 -c 'import sys,json,collections; c=collections.Counter(); [c.update([json.loads(l).get(\"stage\")]) for l in sys.stdin]; print(c)'"

# rejected_short_unsupported gesamt
ssh ubuntu@192.168.178.23 "grep rejected_short /home/ubuntu/ai_analyst_trading_bot/artifacts/bridge_pending_orders.jsonl | wc -l"

# Premium-Signale ohne Operator-Click
ssh ubuntu@192.168.178.23 "grep skipped_source /home/ubuntu/ai_analyst_trading_bot/artifacts/bridge_pending_orders.jsonl | wc -l"
```

## Cross-Refs

- Phase-0-Spec: `docs/security/kai_light_live_phase0_spec.md`
- Phase-0-Pre-Sprints: `docs/security/phase0_pre_sprints.md` (D-222)
- Operator-Runbook: `docs/security/operator_runbook_phase0.md`
- Pre-Sprint A (Lifecycle) deckt Aufgabenpaket 2
- Pre-Sprint B (Recovery) deckt Aufgabenpaket 8
- Pre-Sprint C (Parity) deckt Aufgabenpaket 6
- Pre-Sprint D (AuditStream) deckt Aufgabenpaket 7
- Sprint 1-6 dieses Dokuments deckt Aufgabenpakete 1, 3, 4, 5

## Operator-Decision

Operator-Wahl 2026-05-10: **erst A (Sprint 1 NormalizedTradeSignal + Validator), dann B (Top-3-Bugfixes SHORT/Leverage/Margin)**.
