# KAI Phase-0 Live-Security — Pre-Sprints

**Stand:** 2026-05-10 · **Quelle:** Antigravity-Briefing-Review (Repository-Inspired Architecture Upgrade) · **Status:** binding für Phase-0-Vorbereitung · **Cross-Ref:** `kai_light_live_phase0_spec.md` Tabelle "Implementation-Reihenfolge" (Tasks #1-#12)

## Kontext

Antigravity-Briefing forderte 7-Repo-inspirierten Komplett-Re-Architecture (~5.000 Zeilen Greenfield in `kai/`-Verzeichnis parallel zu `app/`). Gap-Analyse 2026-05-10 ergab: **80 % bereits in V-DB5 / Adaptive-Learning / Phase-0-Spec abgedeckt**. Vier echte Lücken bleiben — und alle vier sind **direkte Voraussetzungen für die existierenden Phase-0-Tasks #7 (`live_engine.py`) und #8 (`live_execution_audit.jsonl`)**. Ohne Pre-Sprints würde `live_engine.py` ein zweites unintegriertes `paper_engine.py`-Analog werden (Antigravity-Mini-MVP-Pattern auf Live-Engine-Steroiden).

Die 4 Pre-Sprints sind als Bedingung VOR Phase-0-Tasks #7/#8 zu erledigen, parallel zu/nach den First-Actions (Operator-Runbook + Exchange-Perm-Verifier `115e357` PR #4 done; Account-Strategie B per Operator-Commit `656ceb5` — Hauptkonto-API-Key direkt, KYC bereits done, kein Sub-Account-Setup; Codex-vault-Drift offen — siehe Memory `session_morgen_pin_20260510.md`).

---

## PRE-SPRINT A — Order-Lifecycle State-Machine in `paper_engine.py`

### Vorschlag
Expliziten Lifecycle-State-Machine `created → validated → armed → submitted → accepted → partially_filled → filled → managed → exit_pending → closed → settled → audited` in `app/execution/paper_engine.py` einziehen — als Pflicht-Vertrag, der von `live_engine.py` (Phase-0-Task #7) übernommen werden muss.

### Warum jetzt?
Phase-0-Task #7 (`live_engine.py`) sagt "analog `paper_engine.py`". Heute hat `paper_engine.py` (749 Z) **keinen expliziten Lifecycle-State-Machine** — Order-States sind implizit über `OrderResult`/`ExecutionState`-Felder verteilt. Wenn `live_engine.py` ohne Pre-Sprint baut, erbt es die Implizität — und Live-Order-Lifecycle (partial fills, exchange-rejects, server-side-SL-conditional-fills) kann nicht zuverlässig auditiert werden. Antigravitys 100-Z-`executor.py` hat das Pattern korrekt, aber unintegriert.

### Erwarteter Nutzen
- Live-Order-Audit (Phase-0-Task #8) bekommt klare State-Transitions als Schema-Felder
- Crash-Recovery (Pre-Sprint B) hat ein klares Wiederaufsetz-Modell pro State
- Paper/Live-Parity (Pre-Sprint C) hat einen formellen Vertrag zum Vergleichen
- Hummingbot-V2-Pattern in der KAI-Codebase ohne fremdes Modul

### Datenquellen / Systeme
- `app/execution/paper_engine.py` (749 Z, Erweiterung)
- `app/execution/models.py` (347 Z, neue State-Enum + Transition-Validator)
- `app/execution/audit_replay.py` (261 Z, State-Transition-Replay)
- `app/orchestrator/decision_journal.py` (Auditpfad)

### Umsetzungsweg
1. Neue Enum `OrderLifecycleState` in `app/execution/models.py` (12 States)
2. Transition-Matrix `LIFECYCLE_TRANSITIONS: dict[State, set[State]]` mit erlaubten Folgestates
3. `paper_engine.py.PaperOrderExecutor` um `_state: OrderLifecycleState` + `_transition(to_state)` mit Matrix-Validierung erweitern (raises `IllegalLifecycleTransition`)
4. Bestehende paper-Pfade (entry, partial-fill-sim, SL/TP-trigger, manual-close) auf explizite Transitionen umstellen — KEIN Verhaltenswechsel, nur State-Tracking
5. Audit-Schema erweitern: jede Transition wird als `lifecycle_event` in `paper_execution_audit.jsonl` geloggt
6. Tests: alle bestehenden paper-Tests müssen grün bleiben + 1 Test pro illegaler Transition (raises)

### Parallel möglich?
Ja. Unabhängig von Pre-Sprint B/C/D. Kann parallel zu Phase-0-Tasks #1-#5 laufen.

### Aufwand
**Realistisch: 6h** (2h State-Definition + Matrix, 3h paper_engine-Refactor, 1h Tests). Blocker: keine. Risiko: Tests-Breaking weil bestehende Pfade nicht alle Transitions explizit machten — adressierbar in 1h Patch-Loop.

### Risiken
- **R1 — Test-Breakage:** Bestehende paper-Tests prüfen vermutlich nur Endzustände, nicht Transitions → Refactor könnte Verhalten subtil ändern. **Mitigation:** State-Tracking als reine Observation-Layer einziehen (read-only), erst dann illegale Transitions als Exception werfen.
- **R2 — Live-Engine-Drift:** Wenn `live_engine.py` (Task #7) später anderen State-Set nutzt, war Pre-Sprint umsonst. **Mitigation:** Pre-Sprint C (Parity-Vertrag) macht das verbindlich.

### Priorität
**P0** (Blocker für Phase-0-Task #7 + #8).

```yaml
ARBEITSPAKET:
  task_id: PHASE0-PRE-A
  phase_id: PHASE-0-Live-Security
  sprint_id: phase0-pre-lifecycle
  titel: Order-Lifecycle State-Machine in paper_engine.py
  warum_jetzt: live_engine.py (Task #7) braucht expliziten Lifecycle-Vertrag, sonst erbt Live-Audit Implizität
  ziel: 12-State-Lifecycle als Pflicht-Vertrag in paper_engine.py
  in_scope:
    - app/execution/models.py (OrderLifecycleState Enum + Matrix)
    - app/execution/paper_engine.py (State-Tracking + Transition-Validator)
    - app/execution/audit_replay.py (State-Replay)
    - tests/unit/test_paper_execution.py (bestehend, erweitern)
    - tests/unit/test_order_lifecycle.py (neu)
  out_of_scope:
    - live_engine.py (das ist Task #7)
    - executor.py von Antigravity (verworfen)
    - Hummingbot-Code-Übernahme (nur Pattern)
  betroffene_module:
    - app/execution/{models,paper_engine,audit_replay}.py
  betroffene_dokumente:
    - DECISION_LOG.md (D-222 reference)
    - kai_light_live_phase0_spec.md (Cross-Ref auf Pre-Sprint A bei Task #7)
  umsetzungshinweise:
    - State als Enum, nicht str
    - Transition-Matrix als FrozenDict
    - kein Verhaltenswechsel, nur Observation-Layer in Phase 1
  tests_erforderlich:
    - alle bestehenden paper-Tests grün
    - 1 Test pro illegaler Transition (raises IllegalLifecycleTransition)
    - 1 Test: vollständiger Lifecycle entry → filled → managed → closed → settled
  validierung:
    - "ruff check + mypy app/execution/"
    - "pytest tests/unit/test_paper_execution.py tests/unit/test_order_lifecycle.py -v"
  akzeptanzkriterien:
    - "paper_execution_audit.jsonl enthält lifecycle_event-Felder pro Transition"
    - "audit_replay kann State-History rekonstruieren"
    - "alle bestehenden V-DB5-Tests bleiben grün"
  risiken:
    - Test-Breakage durch implizite-Transitions (Mitigation: phased Rollout)
    - Live-Engine-Drift (Mitigation: Pre-Sprint C)
  doku_sync_pflicht:
    - kai_light_live_phase0_spec.md Task #7 Cross-Ref
    - app/execution/AGENTS.md (falls vorhanden, Lifecycle-Vertrag dokumentieren)
  naechster_folgeschritt: PHASE0-PRE-C (Paper/Live-Parity-Vertrag baut auf A auf)
```

---

## PRE-SPRINT B — Crash-Recovery-Test (Loop-State + offene Orders)

### Vorschlag
Integration-Test: Trading-Loop wird mid-cycle gekillt, beim Restart muss Loop-State korrekt geladen werden + offene Paper-Orders wieder unter Management stehen + Audit-Stream nahtlos fortgesetzt.

### Warum jetzt?
Phase-0-Spec sagt: "Pi 5 stirbt mid-Trade — offene Position(en) bleiben am Exchange (Server-Side-SL hedged)." Aber: **welche Spur sieht der Position-Monitor beim Restart?** Heute kein Test. NautilusTrader-Pattern fordert: regulärer Start = Crash-Recovery-Pfad. Phase-0-Migration-Drill (Task #10) testet Hardware-Migration, nicht Loop-State-Recovery auf gleicher Hardware.

### Erwarteter Nutzen
- Pi-Reboot-Szenario verifiziert (häufiger als Hardware-Tod)
- Loop-State-Korruption-Bugs früh entdeckt
- Pflicht-Gate vor Live-Aktivierung: "Loop muss Crash-Recovery 3× hintereinander grün haben"

### Datenquellen / Systeme
- `app/orchestrator/trading_loop.py` (Read-only, State-Hooks)
- `app/orchestrator/decision_journal.py` (State-Snapshot)
- `app/execution/paper_engine.py` (offene Orders nach Restart)
- `tests/integration/test_loop_crash_recovery.py` (neu)
- artifacts/trading_loop_audit.jsonl (Vergleich vorher/nachher)

### Umsetzungsweg
1. Test-Setup: pytest-fixture spawnt Loop-Subprocess, lässt 5 Cycles laufen, killt mid-cycle (SIGKILL nach Order-Submit)
2. Restart Loop, lese letzten Cycle aus journal
3. Assert: Loop nimmt offene Order auf, Audit-Stream konsistent (kein Double-Entry, kein Loss)
4. Edge-Case: Crash zwischen `place_order` und `audit_write` → idempotente Recovery via `order_id`-Lookup

### Parallel möglich?
Ja. Unabhängig von Pre-Sprint A/C/D. Phase-0-Task #9 (Tests) parallel.

### Aufwand
**Realistisch: 4-6h** (3h Test-Infrastructure: Loop-Subprocess-Spawn + SIGKILL-Timing, 2h Edge-Cases, 1h CI-Integration). Blocker: existierende Loop-State-Persistence muss komplett sein — wenn Lücken da, Fix-Aufwand +2-4h.

### Risiken
- **R1 — Loop-State-Persistence inkomplett:** Test entdeckt Recovery-Bug → blockt Pre-Sprint, aber EXAKT der Wert dieses Tests. Bug-Fix-Aufwand variabel (1h-1d).
- **R2 — Test-Flakiness durch Subprocess-Timing:** Mitigation via deterministischem Cycle-Trigger (Mock-Clock).

### Priorität
**P1** (parallel zu Phase-0-Task #9 Tests). Eskaliert auf P0 wenn Test einen Recovery-Bug entdeckt.

```yaml
ARBEITSPAKET:
  task_id: PHASE0-PRE-B
  phase_id: PHASE-0-Live-Security
  sprint_id: phase0-pre-crash-recovery
  titel: Crash-Recovery-Test für Trading-Loop + offene Paper-Orders
  warum_jetzt: NautilusTrader-Pattern, Phase-0-Pflicht-Gate vor Live-Aktivierung
  ziel: Loop-Crash-Recovery 3× hintereinander grün
  in_scope:
    - tests/integration/test_loop_crash_recovery.py (neu)
    - tests/integration/conftest.py (Loop-Subprocess-Fixture)
  out_of_scope:
    - Hardware-Migration (das ist Task #10)
    - Live-Engine-Recovery (separat in Phase-0-Tests Task #9)
  betroffene_module:
    - tests/integration/* (neu)
  umsetzungshinweise:
    - Loop als Subprocess starten, SIGKILL via os.kill
    - Mock-Clock für deterministisches Cycle-Timing
    - Idempotenz-Check via order_id-Lookup
  tests_erforderlich:
    - Crash zwischen place_order + audit_write → idempotente Recovery
    - Crash mid-cycle nach Risk-Approval → Cycle wird neu evaluiert
    - Crash nach Order-Fill → Position unter Management nach Restart
  validierung:
    - "pytest tests/integration/test_loop_crash_recovery.py -v --count=3"
  akzeptanzkriterien:
    - "Audit-Stream nach Recovery konsistent (kein Double-Entry)"
    - "Offene Paper-Orders nach Restart unter Management"
    - "Test 3× hintereinander grün"
  risiken:
    - Recovery-Bug-Discovery (Wert > Aufwand)
    - Test-Flakiness (Mock-Clock-Mitigation)
  doku_sync_pflicht:
    - operator_runbook_phase0.md (Pi-Reboot-Sektion mit Recovery-Verifikation)
  naechster_folgeschritt: keine direkte Folge, Voraussetzung für Phase-0-Live-Aktivierung
```

---

## PRE-SPRINT C — Paper/Live-Parity-Vertrag

### Vorschlag
Formaler Pflicht-Vertrag (Code-Interface + Test-Suite) der garantiert, dass `paper_engine.py` und `live_engine.py` (Task #7) **denselben Entscheidungs- und Risiko-Core** nutzen. Differenz nur in Exchange-Adapter (Paper-Mock vs. Live-Binance/Bybit) + Audit-Stream.

### Warum jetzt?
Phase-0-Task #7 sagt "analog `paper_engine.py`". "Analog" ist interpretationsoffen. Briefing fordert NautilusTrader-Pattern "research-to-live semantic parity". Ohne formellen Vertrag wird `live_engine.py` schleichend von paper-Pfad divergieren — und Phase-0-Eskalation auf Phase 1 (Memory: 3-6 Monate Live-Daten) wird mit non-vergleichbaren Daten arbeiten.

### Erwarteter Nutzen
- Garantie: Risk-Engine-Decisions sind paper/live identisch
- Garantie: Lifecycle-State-Transitions sind paper/live identisch (Pre-Sprint A baut Vertrag-Basis)
- Test-Suite, die jede Divergenz sofort sichtbar macht
- Phase-1-Eskalation hat vergleichbare Datenbasis

### Datenquellen / Systeme
- `app/execution/paper_engine.py` (Pre-Sprint A erweitert)
- `app/execution/live_engine.py` (Phase-0-Task #7, neu)
- `app/execution/execution_protocol.py` (neu, abstract base / Protocol)
- `tests/integration/test_paper_live_parity.py` (neu)

### Umsetzungsweg
1. Abstract-Base oder `typing.Protocol` `ExecutionEngineProtocol` definieren — gemeinsame Methoden + State-Felder
2. Refactor: `paper_engine.PaperEngine` implementiert Protocol (aktuell hat es die Methoden ohnehin)
3. Phase-0-Task #7 baut `live_engine.LiveEngine` als Protocol-Implementation — KEIN paralleles Interface
4. Parity-Test-Suite: gleiche `OrderEnvelope` → vergleicht `decision`, `state`, `audit`, `reject_reason` zwischen Paper- und Live-Engine (Live mit Mock-Exchange-Adapter)
5. CI-Hook: Parity-Test bei jedem PR auf `app/execution/` oder `app/risk/`

### Parallel möglich?
Ja, **Voraussetzung**: Pre-Sprint A muss zuerst (Lifecycle-Vertrag). Pre-Sprint C läuft danach, **vor** Phase-0-Task #7.

### Aufwand
**Realistisch: 4h** (1h Protocol-Definition, 2h Parity-Test-Setup, 1h CI-Integration). Blocker: Pre-Sprint A.

### Risiken
- **R1 — Protocol-Drift:** `live_engine.py` hat Live-spezifische Methoden (HOTP-verify, Server-SL), die paper nicht braucht. **Mitigation:** Optional-Methods im Protocol + Default-Implementations, oder klare Erweiterungs-Schnittstelle (`LiveExecutionExtensions`).
- **R2 — Refactor-Friktion:** Bestehender paper-Code passt evtl. nicht 100 % auf Protocol. **Mitigation:** Protocol-Definition aus IST-paper_engine ableiten, nicht vorab erfinden.

### Priorität
**P0** (Blocker für Phase-0-Task #7).

```yaml
ARBEITSPAKET:
  task_id: PHASE0-PRE-C
  phase_id: PHASE-0-Live-Security
  sprint_id: phase0-pre-parity
  titel: Paper/Live-Parity-Vertrag (ExecutionEngineProtocol)
  warum_jetzt: live_engine.py (Task #7) braucht formellen Parity-Vertrag, sonst Divergenz
  ziel: Protocol + Parity-Test-Suite garantiert paper/live-Identität im Decision-Core
  in_scope:
    - app/execution/execution_protocol.py (neu)
    - app/execution/paper_engine.py (Protocol-Konformität)
    - tests/integration/test_paper_live_parity.py (neu)
    - .github/workflows/ci.yml (Parity-Hook)
  out_of_scope:
    - live_engine.py-Implementation (Task #7)
    - HOTP/Exchange-Perm-Live-Spezifika (Live-Extensions, separat)
  betroffene_module:
    - app/execution/{execution_protocol,paper_engine}.py
  abhaengigkeiten:
    - PHASE0-PRE-A (Lifecycle-Vertrag muss erst sein)
  umsetzungshinweise:
    - Protocol aus IST-paper_engine ableiten, nicht vorab erfinden
    - Live-Spezifika als Extensions, nicht im Core-Protocol
    - Mock-Exchange-Adapter für live_engine in Tests
  tests_erforderlich:
    - gleiche OrderEnvelope → identisches decision/state/audit/reject_reason
    - Risk-Engine-Veto: paper + live identisch
    - Lifecycle-Transitions: paper + live identisch
  validierung:
    - "pytest tests/integration/test_paper_live_parity.py -v"
    - "mypy app/execution/"
  akzeptanzkriterien:
    - "ExecutionEngineProtocol als Pflicht-Interface"
    - "Parity-Test grün auf 5 OrderEnvelope-Varianten"
    - "CI hookt Parity-Test bei execution/risk-Änderungen"
  risiken:
    - Protocol-Drift (Live-Extensions-Pattern)
    - Refactor-Friktion (Protocol-IST-derived)
  doku_sync_pflicht:
    - kai_light_live_phase0_spec.md Task #7 Cross-Ref
    - app/execution/AGENTS.md (Parity-Vertrag-Dokumentation)
  naechster_folgeschritt: Phase-0-Task #7 (live_engine.py Implementation)
```

---

## PRE-SPRINT D — AuditStream-Konsolidierung

### Vorschlag
Drei bestehende Audit-Surfaces (`app/audit/kai_audit_service.py`, `app/orchestrator/decision_journal.py`, `app/signals/bayes_journal.py`) auf gemeinsamen Schema-Vertrag konsolidieren, **bevor** `live_execution_audit.jsonl` (Phase-0-Task #8) als 4. Stream entsteht.

### Warum jetzt?
Phase-0-Task #8 plant `live_execution_audit.jsonl` als parallelen Stream zu `paper_execution_audit.jsonl`. Schon heute existieren 3 separate Audit-Schreiber mit unterschiedlichen Schema-Konventionen. Ein 4. Stream ohne Konsolidierung = AuditStream-Drift wird strukturell. Barter-rs-Pattern fordert: ein AuditStream, mehrere Subscriber.

### Erwarteter Nutzen
- Forensik (S-002 in Phase-0-Spec) hat Single-Source-of-Truth
- Dashboard + Telegram lesen denselben Stream
- Phase-1-Eskalation muss nicht 4 inkonsistente Streams reconcilen
- AuditStream-Konsolidierung ist Voraussetzung für Operator-UX-Pflicht aus Phase-0-Spec

### Datenquellen / Systeme
- `app/audit/kai_audit_service.py` (vereinheitlichter Writer, Erweiterung)
- `app/audit/structured_reasoning.py` (Schema-Definition)
- `app/orchestrator/decision_journal.py` (Migration auf kai_audit_service)
- `app/signals/bayes_journal.py` (Migration auf kai_audit_service)
- `tests/unit/test_audit_stream.py` (neu)

### Umsetzungsweg
1. Audit-Schema-Vertrag in `app/audit/structured_reasoning.py` formal definieren (Pydantic-Model `AuditEvent` mit Sub-Types pro Domain)
2. `kai_audit_service.py` als kanonischer Writer mit Domain-Subscriber-Pattern (`emit(event: AuditEvent)`)
3. `decision_journal.py` + `bayes_journal.py` als Subscriber adaptieren — bestehende JSONL-Dateien bleiben bestehen, aber Writes gehen durch zentralen Service
4. Migration-Test: alte JSONL-Files bleiben lesbar (Backward-Compat)
5. Phase-0-Task #8 (`live_execution_audit.jsonl`) wird 4. Subscriber, kein paralleler Schreiber

### Parallel möglich?
Ja. Unabhängig von Pre-Sprint A/B/C. Vor Phase-0-Task #8 erforderlich.

### Aufwand
**Realistisch: 6-8h** (2h Schema-Definition, 3h Service-Refactor, 2h Subscriber-Migration, 1h Tests). Blocker: keine. Risiko: bestehende Reader (Dashboard, Telegram) müssen Schema-Versionierung respektieren — adressierbar via `schema_version`-Feld.

### Risiken
- **R1 — Backward-Compat-Bruch:** Bestehende JSONL-Reader (Dashboard, Telegram, Replay-Tools) crashen wenn Schema sich ändert. **Mitigation:** Schema-Versionierung (`schema_version: "audit-v1"` → `"audit-v2"`), Reader-Fallback-Pfad.
- **R2 — Refactor-Scope-Creep:** "Konsolidierung" kann sich auf Dashboard-Reader/Telegram-Renderer ausweiten. **Mitigation:** Pre-Sprint scope = Writer-Konsolidierung. Reader bleiben intakt, nutzen versionierte Reader-Adapter.

### Priorität
**P0** (Blocker für Phase-0-Task #8).

```yaml
ARBEITSPAKET:
  task_id: PHASE0-PRE-D
  phase_id: PHASE-0-Live-Security
  sprint_id: phase0-pre-audit-stream
  titel: AuditStream-Konsolidierung (3 Surfaces → 1 Service)
  warum_jetzt: live_execution_audit.jsonl (Task #8) als 4. paralleler Stream wäre struktureller Drift
  ziel: Single-Service kai_audit_service als kanonischer Writer, andere als Subscriber
  in_scope:
    - app/audit/{kai_audit_service,structured_reasoning}.py
    - app/orchestrator/decision_journal.py (Subscriber-Migration)
    - app/signals/bayes_journal.py (Subscriber-Migration)
    - tests/unit/test_audit_stream.py (neu)
  out_of_scope:
    - Reader-Side (Dashboard, Telegram) — bleiben intakt mit Schema-Versionierung
    - live_execution_audit.jsonl (das ist Task #8, baut auf D auf)
  betroffene_module:
    - app/audit/*
    - app/orchestrator/decision_journal.py
    - app/signals/bayes_journal.py
  umsetzungshinweise:
    - Pydantic AuditEvent als Schema-Vertrag mit Sub-Types
    - Backward-Compat via schema_version-Feld
    - bestehende JSONL-Files bleiben lesbar
  tests_erforderlich:
    - alle bestehenden audit-Tests grün
    - 1 Test pro Subscriber-Pfad (decision/bayes)
    - 1 Test: Schema-Versionierung Reader-Fallback
  validierung:
    - "pytest tests/unit/test_kai_audit_service.py tests/unit/test_audit_stream.py -v"
    - "mypy app/audit/"
  akzeptanzkriterien:
    - "kai_audit_service.emit() ist einziger Writer-Pfad"
    - "decision_journal + bayes_journal sind Subscriber"
    - "Schema-Versionierung dokumentiert"
  risiken:
    - Backward-Compat-Bruch (Schema-Versionierung mitigiert)
    - Scope-Creep auf Reader (out_of_scope-Disziplin)
  doku_sync_pflicht:
    - kai_light_live_phase0_spec.md Task #8 Cross-Ref
    - DECISION_LOG.md (D-222)
  naechster_folgeschritt: Phase-0-Task #8 (live_execution_audit.jsonl als Subscriber #4)
```

---

## Reihenfolge & Aufwand-Total

| Reihenfolge | Pre-Sprint | Aufwand | Blockt Phase-0-Task | Parallel-Tag |
|---|---|---|---|---|
| 1 | **PRE-A** Order-Lifecycle | 6h | #7 | parallel zu #1-#5 |
| 2 | **PRE-D** AuditStream-Konsolidierung | 6-8h | #8 | parallel zu #1-#5 |
| 3 | **PRE-C** Paper/Live-Parity (braucht A) | 4h | #7 | nach PRE-A, parallel zu #1-#6 |
| 4 | **PRE-B** Crash-Recovery-Test | 4-6h | (Live-Aktivierungs-Gate) | parallel zu #9 |

**Total Pre-Sprints:** ~20-24h ≈ **3 Arbeitstage** Solo. Verteilbar parallel zu First-Actions (Operator-Runbook + Exchange-Perm-Verifier done `115e357`; Account-Strategie B done `656ceb5` — Hauptkonto direkt, KYC done, kein Sub-Account; Codex-vault-Drift offen) und Phase-0-Tasks #1-#5.

**Nicht parallel:** PRE-A muss vor PRE-C. Beide müssen vor Phase-0-Task #7. PRE-D muss vor Phase-0-Task #8.

## Out-of-Scope (bewusst nicht eingereiht)

Aus Antigravity-Briefing **nicht** übernommen — Begründung pro Punkt:

- **Greenfield `kai/`-Verzeichnis:** Kollidiert mit `app/`-Bestand (27 Subpackages, ~5.000 Z V-DB5+Execution). Re-Build statt Integration.
- **Multi-Agent-Committee mit `stance: bullish/bearish`:** Widerspricht Memory-Leitsatz "KAI darf den Markt nicht vorhersagen" (`feedback_kai_no_prediction.md`). Wenn Multi-Agent später, dann mit `evidence_strength`/`uncertainty`/`risk_level` statt Stance.
- **Komplettes Backtest-Engine-Re-Build (Freqtrade-Style):** `app/execution/backtest_engine.py` (434 Z) existiert. Erweiterung statt Re-Build, falls Lücken sichtbar werden — eigener Sprint-Slot, nicht Phase-0-Pre.
- **DataQualityGate-Re-Build:** Bestehende `app/data/quality_gate.py` (115 Z, untracked auf adaptive-learning) ist Skelett — Erweiterung gehört zur **Datenqualitäts-Stufe** der Priority-Reorder-Reihenfolge (Memory 2026-05-09: Daten → Risiko → Regime → ...), nicht Phase-0-Pre.

## Cross-Refs

- Phase-0-Spec: `kai_light_live_phase0_spec.md`
- Operator-Runbook: `operator_runbook_phase0.md`
- Decision-Log: `decision_log_20260509.md` (D1=B Light-Live), `DECISION_LOG.md` (D-222 Pre-Sprint-Entscheidung)
- Memory: `kai_live_trading_security_phase0.md`, `session_morgen_pin_20260510.md`, `kai_phase0_pre_sprints_20260510.md`, `feedback_kai_no_prediction.md`, `feedback_multi_agent_drift_branch_pattern.md`
- Briefing-Quelle: Antigravity-Architektur-Upgrade-Prompt (2026-05-10, gap-analyzed + abgelehnt für Re-Build, 4 Lücken extrahiert)
