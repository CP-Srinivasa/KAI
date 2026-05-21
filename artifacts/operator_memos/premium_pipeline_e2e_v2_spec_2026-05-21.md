# Premium-Pipeline E2E V2 — Partial-Entry-Fill Spec

**Status:** Spec only, kein Code. Folge-Sprint zu V1 (commit `e115fec9` auf
`claude/p7/reentry-ia-codex-cycle`, 2026-05-21).
**Autor:** Claude Code, 2026-05-21.
**Vorab-Recherche:**
- V1-Datei: `tests/integration/test_premium_pipeline_e2e.py` (2 Cases, SOL Fill + IRYS Reject).
- Paper-Engine partial-fill-Mechanik: voll implementiert via `partial_fill_ratio` Parameter (`app/execution/paper_engine.py:176`), 6 Unit-Tests in `test_paper_execution.py:854-941`.
- EntryRangeWatcher: pure observation (`app/execution/entry_watcher.py`), emittiert WAITING_FOR_ENTRY -> ENTRY_TRIGGERED/EXPIRED, platziert KEINE Orders.
- OperatorEntryWatch: HF-Polling-Brücke (`app/execution/operator_entry_watch.py`), ruft beim Hit `envelope_to_paper_bridge.run_tick()` — Single-Path-Idempotenz.
- **Gap:** `envelope_to_paper_bridge` ruft `submit_order(...)` ohne `partial_fill_ratio` Parameter — Paper-Partial-Mechanik ist via Bridge nicht erreichbar. Bridge-Default ist immer `partial_fill_ratio=1.0`.

---

## Vorschlag

`tests/integration/test_premium_pipeline_e2e.py` um drei E2E-Test-Cases erweitern, die das **Multi-Tick-Verhalten** des Premium-Range-Entry-Pfads abdecken. Kein Core-Code geändert (analog V1).

## Warum jetzt?

V1 deckt nur Single-Tick-Fill und Single-Tick-Reject ab. Real liefern Premium-Channel-Signale **Entry-Ranges** (z.B. SOL 84.20-84.50), die über mehrere Bridge-Ticks beobachtet werden, bis der Preis den Range trifft. Das aktuelle Test-Setup beweist nicht, dass:

1. Eine Range-Entry mit Preis **außerhalb** Range im ersten Tick `pending` bleibt und im **zweiten** Tick füllt.
2. Eine Range-Entry, die **nie** getroffen wird, sauber per TTL **expires** statt liegenzubleiben.
3. Ein Fill mit `partial_fill_ratio<1.0` (z.B. durch RiskEngine-Slippage oder zukünftige Bridge-Erweiterung) eine **PaperPosition mit Teilquantität** öffnet und der Follow-up-Fill die Restquantität nachzieht.

Punkt 3 ist Test-Spec, nicht Feature-Spec: paper_engine kann partial-fill bereits, Bridge reicht es aktuell nicht durch. V2.3 demonstriert das Verhalten unter `monkeypatch.setattr(paper_engine, "create_order", ...)`-Injektion und ist damit eine Vorbereitung für eine spätere Bridge-Erweiterung — keine Spezifikation einer solchen Erweiterung.

## Erwarteter Nutzen

- Lebende Regressions-Guard fuer den Multi-Tick-Bridge-Pfad — bevor wir an EntryRangeWatcher / Bridge-Race-Conditions Folgearbeit hängen.
- Frühe Sichtbarkeit für TTL-Expiry-Pfad — heute existiert ein Unit-Test fuer `_ttl_exceeded`, aber kein E2E ueber Parser->Approval->Bridge->Audit.
- Vorbereitete Test-Surface für eine spätere Bridge-Erweiterung `submit_order(partial_fill_ratio=...)`, falls RiskEngine-Liquidity-Slippage ins Paper-Sizing einfließen soll.

## Datenquellen / Systeme

Identisch zu V1, plus:
- `app/execution/entry_watcher.py` (nur als Hintergrund, nicht direkt aufgerufen — Bridge bleibt der Test-Einstiegspunkt).
- `paper_engine.create_order(partial_fill_ratio=...)` als monkeypatch-Target in V2.3.
- Settings `EXECUTION_OPERATOR_SIGNAL_TTL_HOURS` für V2.2.

## Umsetzungsweg

### V2.1 — Range-Entry Multi-Tick Fill

**Fixture:** SOL/USDT Range-Entry 84.20-84.50, gleicher Premium-Channel-Stil wie V1.

**Ablauf:**
1. `_emit_and_approve(...)` (identische Helper aus V1).
2. **Tick 1:** `price_provider` liefert 84.10 (unter Range) -> `result.filled == 0`, `result.skipped_*` zaehlt diesen Envelope, Bridge-Stage bleibt `pending`.
3. **Tick 2:** `price_provider` liefert 84.30 (in Range) -> `result.filled == 1`, Bridge-Stage `filled`, PaperPosition `SOL/USDT` qty>0.

**Assertions:**
- Tick 1: `bridge_records[-1]["stage"] == "pending"`, kein `lifecycle_transition` mit `to_state="POSITION_OPEN"`.
- Tick 2: identische correlation_id-Chain wie V1, plus Assertion dass die Tick-2-Audit-Records TIMESTAMP > Tick-1 haben (kein Race-Condition-Replay).
- `engine.portfolio.positions["SOL/USDT"].quantity > 0` exakt einmal — kein doppelter Fill.

### V2.2 — Range-Entry TTL-Expiry ohne Fill

**Fixture:** SOL/USDT Range-Entry 84.20-84.50, `EXECUTION_OPERATOR_SIGNAL_TTL_HOURS=1`.

**Ablauf:**
1. `_emit_and_approve(..., emitted_at=T0, approved_at=T0+3min)`.
2. **Tick 1 (T0+10min):** price 90.00 (weit außerhalb) -> `pending`.
3. **Tick 2 (T0+70min, ueber TTL):** price 84.30 (in Range) -> Bridge-Stage `expired`, **kein Fill**.

**Assertions:**
- `result.filled == 0`, `result.expired == 1`.
- `bridge_records[-1]["stage"] == "expired"`.
- Kein Eintrag in paper_audit, keine PaperPosition für SOL/USDT.
- Letzte Lifecycle-Transition referenziert `EXPIRED` (oder Bridge-spezifischer Terminal-State).

**Annahme:** Die Bridge prüft TTL vor Fill — wenn nicht, Test wird rot und enthuellt eine Pipeline-Lücke (gewollter Outcome, kein Test-Bug).

### V2.3 — Partial-Fill durchläuft (paper_engine-Patch)

**Fixture:** ETH/USDT Range-Entry 3000-3010, RiskEngine berechnet Quantität=2.0.

**Mechanik:**
- `monkeypatch.setattr(paper_engine_module.PaperExecutionEngine, "create_order", patched_create_order)` wo `patched_create_order` an die Original-Methode `partial_fill_ratio=0.5` durchreicht.
- Wichtig: keine Änderung an `envelope_to_paper_bridge.py` selbst.

**Ablauf:**
1. `_emit_and_approve(...)`.
2. **Tick 1:** price 3005.0 (in Range) -> Bridge submits Order, paper_engine fillt nur 50% (=1.0 ETH).

**Assertions:**
- `paper_audit` enthaelt `order_filled` mit `fill_status="partial_entry"`, `filled_quantity == 1.0`, `remaining_quantity == 1.0`.
- PaperPosition `ETH/USDT` qty == 1.0.
- `correlation_id` auf Order, Position und Audit identisch mit origin_envelope_id.
- `bridge_records[-1]["stage"] == "filled"` (Bridge sieht den Fill, kennt aber `partial` nicht — dokumentierte Limitation).

**Hinweis im Test-Docstring:** "Bridge reicht partial_fill_ratio heute nicht durch (Default 1.0). Dieser Test simuliert die paper-seitige Partial-Mechanik per monkeypatch und garantiert, dass eine spätere Bridge-Erweiterung den E2E-Pfad nicht regrediert."

### V2.4 (optional, P3) — Multi-Tick mit Plausibility-Outlier

**Nur wenn V2.1-V2.3 grün laufen** und Bandbreite frei ist.

EntryRangeWatcher hat einen rolling-median Plausibility-Filter (`plausibility_max_deviation_pct`). Ein 95.00-Tick zwischen 84.30-Ticks sollte rejected werden. V2.4 würde das integriert testen — aber EntryRangeWatcher ist nicht im Bridge-Tick-Pfad, sondern eine HF-Brücke darueber. Test wäre `app.execution.operator_entry_watch.process_signals_step()` aufrufen, nicht `bridge.run_tick()`. Macht V2.4 zu einem separaten Test-Pfad — daher P3 + separater Sprint.

## Parallel möglich?

Ja. V2.1, V2.2, V2.3 sind unabhängige Test-Cases ohne shared state (jede nutzt eigene tmp_path-Isolation, eigene `_emit_and_approve`-Aufrufe). Können in einer Session sequenziell oder parallel implementiert werden — bei Bridge-Race-Condition-Verdacht sequenziell.

## Aufwand

- V2.1 ~25 min (Helper aus V1 wiederverwenden, 2 Tick-Calls, ~70 Test-Zeilen).
- V2.2 ~25 min (TTL-Setting + zweiter datetime-Wert, ~60 Zeilen).
- V2.3 ~40 min (monkeypatch-Pattern aufbauen, ~80 Zeilen).
- V2.4 ~60 min, separater Sprint.

**Gesamt V2.1-V2.3:** ~1.5h inkl. ruff/mypy/pytest-Validierung. Mid-Session machbar.

## Risiken

- **R1 — TTL-Path-Realitäts-Check (V2.2):** Wenn die Bridge TTL erst nach einem Fill-Versuch prueft, bricht V2.2 anders ab als spezifiziert. Mitigation: vor Implementation den TTL-Pfad in `envelope_to_paper_bridge.run_tick()` und `_ttl_exceeded` 5min lesen, Test entsprechend justieren oder die Pipeline-Lücke explizit als Test-Failure dokumentieren.
- **R2 — Bridge-Race-Guard (V2.1):** Bridge hat seit 2026-05-12 Sprint C einen cross-process Race-Guard (Stage `filled_duplicate_suppressed`). Wenn zwei `run_tick()`-Calls schnell nacheinander mit gleichem envelope passieren, könnte Tick 2 als duplicate-suppressed audited werden. Mitigation: zwischen Tick 1 und Tick 2 deutlich unterschiedliche `now`-Werte injizieren, Stage-Check tolerant gegenüber `pending` und `filled_duplicate_suppressed`.
- **R3 — Singleton-PaperEngine-Bleed (V2.3):** Existing V1-Tests nutzen `get_paper_engine()` Singleton — V2.3 muss zwischen Tests die Engine resetten oder ein eigenes singleton-isoliertes Setup. Mitigation: V1-Setup-Mechanik prüfen (gibt es eine Reset-Funktion oder muss man PaperExecutionEngine direkt instanziieren?).
- **R4 — Partial-Fill ohne Spec-Anker:** V2.3 testet ein Verhalten, das in production nie ausgeloest wird (Bridge schickt nie partial_fill_ratio<1.0). Risiko: Test wird zum "Test-Theater" wenn die Bridge nie partial-fill bekommt. Mitigation: Docstring macht explizit klar, dass V2.3 die paper-Schicht absichert, nicht eine Bridge-Erweiterung beweist. Wenn das Bridge-Feature nie kommt, V2.3 closen statt durchschleifen.

## Priorität

**V2.1 + V2.2: P1.** Decken zwei sehr realistische Production-Szenarien ab (Range-Entry wird nicht im ersten Tick getroffen / Range wird nie getroffen + TTL läuft ab).
**V2.3: P2.** Vorbereitung. Bringt Wert wenn Bridge-Erweiterung kommt, sonst Test-Pflege ohne Production-Anker.
**V2.4: P3.** Separater Test-Pfad ueber OperatorEntryWatch, eigenes Sprint-Paket.

## Test-Befehl

```
pytest tests/integration/test_premium_pipeline_e2e.py -v
ruff check tests/integration/test_premium_pipeline_e2e.py
mypy tests/integration/test_premium_pipeline_e2e.py
```

## Akzeptanzkriterien

V2 ist "done" wenn:
- V2.1, V2.2, V2.3 als Test-Cases existieren und grün laufen.
- Gesamtlaufzeit der File bleibt < 5s (V1 war 1.2s; jeder neue Case <1s).
- ruff + mypy clean.
- Kein Core-Code geändert (verifiziert per `git diff --stat HEAD app/`).
- Datei in git committed (nicht nur lokal).
- Wenn V2.2 eine Bridge-TTL-Lücke aufdeckt: Lücke als separates Memo `bridge_ttl_gap_2026-05-XX.md` dokumentieren, nicht still fixen.

## Out-of-Scope

- Telegram MTProto / Telethon Transport (wie V1).
- Live-Engine partial-fill (separater Pfad, andere Spec).
- EntryRangeWatcher-Tests die nicht via Bridge laufen (V2.4 explizit P3).
- Bridge-Feature `submit_order(partial_fill_ratio=...)` durchreichen — eigenes Sprint-Paket falls Operator-Bedarf entsteht.

## Related

- V1-Commit `e115fec9`.
- V1-Datei `tests/integration/test_premium_pipeline_e2e.py`.
- Memory `[[kai-premium-signal-pipeline-e2e-fix-20260512]]` (Auto-Fill + EntryRangeWatcher-Aktivierung).
- Memory `[[session_2026_05_10_signal_pipeline_drift]]` (Sprint-B-Bug-Liste, Race-Guard-Kontext).
- `app/execution/envelope_to_paper_bridge.py` (Source der `run_tick`-Mechanik).
- `app/execution/paper_engine.py:176-201` (partial_fill_ratio validation).
- `tests/unit/test_paper_execution.py:854-941` (existierende Partial-Unit-Tests).
