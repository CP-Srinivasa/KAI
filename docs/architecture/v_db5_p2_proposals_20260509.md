# V-DB5 P2-Architektur-Items — 6 Vorschläge im Pflichtformat

**Datum:** 2026-05-09
**Quelle:** V-DB5-Audit-Backlog vom 2026-05-08 (architecture-red-team + neo + data-quality-inspector + dali, 4-Subagent-Tiefen-Audit, P2-Tier).
**Status der P1-UI-Tranche:** ✅ deployed auf Pi 5 (commit `8712768`, 2026-05-09 13:46 UTC).
**Format:** CLAUDE.md § 11 Pflichtformat. Operator-Sign-off pro Item nötig.

> **Querverweis:** Diese 6 Items stehen in der Architektur-Audit-Roadmap unter Phase 2 (Signal Quality), Phase 4 (Decision Gates), Phase 5 (Dashboard). Siehe `kai_master_audit_20260509.md` für Phasen-Einordnung.

---

## Vorschlag 1 — Sticky-Highwater + Wilson-Floor im Re-Entry-Gate

### Vorschlag
Re-Entry-Gate auf Sticky-Highwater-Logik mit zusätzlichem Wilson-Quality-Floor umstellen.

### Warum jetzt?
Die aktuelle OR-Logik (Pfad A ≥200 alerts ODER Pfad B ≥10 fills) liest **Live-State** ohne Highwater. Wenn Pfad B kurzfristig auf 5/10 zurückfällt, kippt das Gate erneut auf "open" — operativ irreführend. Pfad A hat zusätzlich keinen Quality-Floor: 200 alerts mit 49% hit-rate würden das Gate erfüllen, obwohl die Wilson-Lower-Bound unter dem 60%-Threshold liegt. Operator hat heute ein Re-Entry-Gate, das gegen seine eigene Logik widersprüchlich melden kann.

### Erwarteter Nutzen
- **Operativ**: kein "flackern" zwischen open/closed bei Marginal-Schwellen. Einmal "geöffnet" bleibt das Gate offen, bis ein expliziter Reset-Befehl kommt.
- **Forensisch**: Sticky-Status-Zeitstempel im decision_journal — Operator kann nachvollziehen *wann* das Gate eröffnet wurde.
- **Quality**: Wilson-Floor schließt aus, dass 200 niedrig-trefferquote-Alerts das Gate aufmachen.

### Datenquellen / Systeme
- `web/src/components/panels/ReentryGatePanel.tsx:36` — Frontend-Eval-Logik
- Neuer Backend-Pfad in `app/alerts/hold_metrics.py` oder `app/alerts/eligibility.py` mit Sticky-State (Persistenz in `artifacts/reentry_gate_state.json` oder DB-Tabelle)
- `dashboard/api/quality` liefert bereits `priority_tier_high_conviction_ci_low_pct` — Re-Use

### Umsetzungsweg
1. Backend-Modul `app/alerts/reentry_gate.py` mit dataclass `ReentryGateState{opened_at, opened_via_path, last_eval_at, sticky}` + Persistenz in JSONL/DB.
2. Eval-Funktion `evaluate_reentry_gate(metrics) -> ReentryGateState`: liest persistenten State, prüft Pfad A (≥200 alerts UND Wilson-Lower ≥60%) ODER Pfad B (≥10 fills mit positiver Forward-Precision). Wenn `state.sticky == True`, bleibt es offen.
3. Reset-CLI `kai cli alerts reentry-gate reset` für Operator.
4. Backend-Endpoint `/dashboard/api/quality.reentry_gate` liefert State-Snapshot.
5. Frontend-Eval entfernen, nur State anzeigen.
6. Test-Suite: Sticky-Persistenz, Wilson-Floor-Effekt, Reset-Flow.

### Parallel möglich?
**Ja**, da reines Backend-Modul + Frontend-Read-only. Konfliktfrei mit Phase-2-Signal-Score-Engine (anderes Modul). Konflikt nur mit Codex-WIP an `eligibility.py` (im V-DB5-Backend-Stash) — erst nach Rescue-Commit starten.

### Aufwand
**Realistisch**: 4-6h Code + 1-2h Tests + 30min Pi-Deploy + Smoke. Total ~1 Arbeitstag.

### Risiken
- **Technisch**: Sticky-State-Persistenz auf JSONL (Append) vs. DB (Update) — Operator-Decision nötig. Empfehlung: JSONL (konsistent zu paper_execution_audit), `latest`-Helper für Read.
- **Operativ**: Sticky-Reset-CLI kann versehentlich aufgerufen werden — `--confirm`-Flag pflichtig.
- **Quality**: Wilson-Floor-Threshold (60%) muss aus Settings, nicht hardcoded.
- **Fail-Closed-Disziplin**: bei fehlendem State-File muss Gate `closed` zurückgeben, nicht `open`.

### Priorität
**P2** — verbessert Operator-Klarheit + Quality-Disziplin, aber kein P0/P1 (V-DB5-P1-UI hat das visuelle Drift-Problem schon entschärft).

---

## Vorschlag 2 — Forward-Precision Watchdog mit Telegram-Alert

### Vorschlag
Telegram-Alert wenn `forward_precision_pct < 60%` oder `forward_precision_ci_low_pct < 50%` über N aufeinanderfolgende Stunden.

### Warum jetzt?
Heute ist Forward-Precision-Drift **silent** — Operator merkt das erst, wenn er manuell ins Dashboard schaut. Das Anti-Pattern ist genau die KAI-Live-Phase-2-Lehre: stille Failure-Modes verstecken Probleme stundenlang. Gestern hat der gleiche Pattern den Market-Data-Provider-Symmetrie-Bug 24h verborgen (`paper_engine.rehydrate` war stumm-erfolgreich).

### Erwarteter Nutzen
- **Operativ**: Operator wird innerhalb 1h informiert, wenn Forward-Signal an Qualität verliert. Kann reagieren, bevor blocked-alerts hochlaufen.
- **Auditfähigkeit**: Watchdog-Findings als JSONL in `artifacts/agents/watchdog/findings.jsonl` mit `cross_ref` zum letzten quality-snapshot.
- **Selbstüberwachung**: KAI meldet eigene Quality-Drift, nicht nur externe Source-Drift.

### Datenquellen / Systeme
- `app/alerts/hold_metrics.py` (oder neue `app/alerts/forward_precision_watchdog.py`)
- Trigger via `kai-hold-report.timer` (existiert daily 5:00 UTC) oder neuer `kai-watchdog.timer`
- Telegram-Push via `app/messaging/telegram_bot.py:send_operator_message()` oder `app/agents/worker.py:_publish_finding()` mit Telegram-Pflicht
- Settings-Felder `APP_WATCHDOG_FORWARD_PRECISION_THRESHOLD_PCT` (default 60), `APP_WATCHDOG_FORWARD_PRECISION_CI_LOW_THRESHOLD_PCT` (default 50), `APP_WATCHDOG_FORWARD_PRECISION_CONSECUTIVE_HOURS` (default 6)

### Umsetzungsweg
1. Helper `_check_forward_precision_drift(report) -> WatchdogFinding | None` in Watchdog-Worker.
2. Persistente State-Datei `artifacts/watchdog/forward_precision_streak.json` mit `{streak_hours, last_below_threshold_at}`. Reset bei Recovery, accumulate bei consecutive below.
3. Bei `streak_hours >= APP_WATCHDOG_FORWARD_PRECISION_CONSECUTIVE_HOURS`: Finding mit Severity `warn`, Telegram-Push, finding.jsonl-Eintrag.
4. Anti-Spam: Cooldown 12h zwischen Push-Notifications für die gleiche Drift-Phase.
5. Reset-Push wenn Recovery erkannt.
6. Tests: streak-Akkumulation, Recovery-Reset, Cooldown.

### Parallel möglich?
**Ja**. Reines Watchdog-Modul, keine Frontend-Änderung. Konfliktfrei mit anderen P2-Items.

### Aufwand
**Minimal**: 2-3h Code + 1h Tests + 30min Deploy. Total ~halber Arbeitstag.

### Risiken
- **Operativ**: Alert-Spam bei volatilem Forward-Signal — Cooldown und Streak-Threshold müssen sinnvoll sein. Empfehlung: Default 6h Streak + 12h Cooldown.
- **Technisch**: State-File-Race-Condition wenn zwei Watchdog-Runs parallel — portalocker analog zu V-DB5 B-K2.
- **Quality**: Schwellwerte aus Settings, nicht hardcoded — sonst Drift-Risiko bei Kalibrierungs-Updates.

### Priorität
**P1** (höher als V-DB5-Memo-Einstufung "P2"), weil silent-failure-Modes operativ extrem gefährlich sind. Nach Phase-1-Rescue starten.

---

## Vorschlag 3 — Domain-Hash vs Storage-Hash Drift fixen

### Vorschlag
`CanonicalDocument._compute_hash` entfernen ODER auf normalisierte Werte umstellen, sodass Domain-Hash und `prepare_ingested_document.content_hash` deckungsgleich sind.

### Warum jetzt?
`app/core/domain/document.py:184` (`_compute_hash`) nutzt rohe Werte (`url|title|raw_text`). `app/storage/document_ingest.py:107` überschreibt mit normalisiertem `content_hash`. Latent-Bug bei Test-Pfaden, die `DocumentRepository.save_document` direkt aufrufen — Hash divergiert zwischen Domain-Layer und Storage-Layer. Im V-DB5-Audit wurde das als F-005 markiert (data-quality-inspector). Ohne Fix: Dedup kann auf falschen Hash laufen.

### Erwarteter Nutzen
- **Daten-Integrität**: ein Document hat *einen* Hash, überall. Dedup-Logik wird vorhersagbar.
- **Test-Stabilität**: Tests, die `save_document` direkt aufrufen, treffen denselben Hash wie der Pipeline-Pfad.
- **Audit-Klarheit**: Hash im Audit-JSONL ist eindeutig dem Source-Document zuordenbar.

### Datenquellen / Systeme
- `app/core/domain/document.py:184` — `CanonicalDocument._compute_hash`
- `app/storage/document_ingest.py:107` — `prepare_ingested_document` Norm-Pipeline
- Tests: `tests/unit/test_document_repository.py`, `tests/unit/test_canonical_document.py`

### Umsetzungsweg
**Option A (empfohlen)**: `_compute_hash` aus Domain-Layer entfernen. `content_hash` ausschließlich in Ingest-Pipeline gesetzt. Domain-Constructor verlangt expliziten `content_hash`-Parameter.

**Option B**: `_compute_hash` auf normalisierte Werte umstellen (Lowercase, Trim, NFC-Normalize). Pipeline-Pfad muss Domain-Form vor Hash-Compute aufrufen.

1. Audit existierender Aufrufstellen (`grep -rn _compute_hash`)
2. Migration-Plan: alte Audits mit altem Hash bleiben — Repository-Read fällt auf `legacy_hash`-Field zurück
3. Code-Change + Tests + Migration-Note in `migrations/`
4. Live-Audit: laufender Pi-State auf Hash-Drift prüfen (sollte minimal sein)

### Parallel möglich?
**Begrenzt** — kollidiert mit Codex' Evidence-Schema-WIP (`app/core/evidence.py` aus V-DB5-Backend-Stash), weil Evidence einen `content_fingerprint` hat. Empfehlung: **erst Phase-1-Evidence-Schema live**, dann Domain-Hash-Drift mit der Evidence-Logik harmonisieren (gleicher Algo: SHA-256 über kanonisierte Felder).

### Aufwand
**Realistisch**: 3-5h Code + 2h Tests + 1h Migration + Deploy. Total ~1 Arbeitstag.

### Risiken
- **Technisch**: Hash-Migration kann Dedup-Sets invalidieren — alte Document-Rows bekommen neuen Hash. Migration-Strategie: `legacy_hash`-Field beibehalten, neue Writes nur `content_hash`.
- **Audit-Konsistenz**: alte audit-rows mit altem Hash bleiben — Repository-Read muss beide kennen.
- **Coupling**: Phase-1-Evidence-Schema sollte denselben Hash-Algo nutzen, sonst doppelte Logik.

### Priorität
**P3** — kein operativer Schmerz heute (Pipeline-Pfad funktioniert), aber **Schuld** der mit Phase-1-Evidence in einem Aufwasch behoben wird. Empfehlung: koppeln an Evidence-Schema-Live.

---

## Vorschlag 4 — 3-Source-Panels in Tab-Container "Source-Performance" konsolidieren (V-DB6-Sprint)

### Vorschlag
ActivePrecisionCard + PerSourcePrecisionPanel + PerSourceStabilityPanel in einen Tab-Container "Source-Performance" mit Tabs ["Aktuell" / "Stabilität" / "Baseline"] zusammenführen.

### Warum jetzt?
Aktuell rendert das Dashboard 3 Source-Tabellen untereinander mit divergentem Naming/Format/Display. V-DB5-P1-Tranche heute hat das Source-Display via `lib/sourceLabels.ts` schon harmonisiert — aber die strukturelle Doppelung bleibt: Operator scrollt durch 3 ähnliche Tabellen statt einer geordneten Drilldown-View. DALI-Audit (V-DB5 H-1) hat das als P2-Architektur-Item markiert: Tab-Container spart vertikalen Platz, schafft Hierarchie, vermeidet weiteres Source-Display-Drift.

### Erwarteter Nutzen
- **UI**: ~600px vertikaler Platz gespart auf Dashboard.
- **Cognitive Load**: ein Source-Performance-Block, drei Sichten — klarer als drei nebeneinander stehende Karten.
- **Wartung**: zentrale Tab-Component statt 3 Panel-Logiken; Source-Tone-Logik einmal definiert.

### Datenquellen / Systeme
- `web/src/components/panels/ActivePrecisionCard.tsx` (heute Provenance-View)
- `web/src/components/panels/PerSourcePrecisionPanel.tsx` (heute Active-Precision-View)
- `web/src/components/panels/PerSourceStabilityPanel.tsx` (heute 30d-Stability-View)
- `web/src/pages/Dashboard.tsx` Render-Section
- Neue `web/src/components/panels/SourcePerformancePanel.tsx` (Composer)
- Neue Tab-Component `web/src/components/ui/Tabs.tsx` (oder Re-Use bestehender `Disclosure`-Pattern)

### Umsetzungsweg
1. **Vorbereitung**: 3 Panels heute (post-V-DB5) bereits visuell konsistent (Tone-Dot, deutsche Microcopy, sourceLabels.ts geteilt) — nicht wegwerfen, sondern als Tab-Inhalte verwenden.
2. Neue `SourcePerformancePanel`-Composer-Komponente: Tabs ["Aktuell" / "Stabilität" / "Baseline"] mit Lazy-Render.
3. Tabs-Primitive in `ui/Tabs.tsx` (Headless-Pattern, A11y-konform mit `role="tablist"`/`role="tab"`/`aria-controls`).
4. Dashboard.tsx: 3 Panel-Imports → 1 Composer-Import. Card-Layout adaptieren.
5. Tests: Tab-Wechsel, Lazy-Render, A11y-Keyboard-Nav.
6. Pi-Deploy + Smoke.

### Parallel möglich?
**Ja**, aber Konflikt mit anderen UI-Branches (KAI-Live-Phase-2 hat Dashboard.tsx auch im Working-Tree). Empfehlung: eigener Sprint **V-DB6**, nach Phase-5 Dashboard-Surface.

### Aufwand
**Realistisch**: 6-8h Code (Tabs-Primitive 3h + Composer 3h + Dashboard-Integration 2h) + 2h Tests + 1h Pi-Deploy + Smoke. Total ~1.5 Arbeitstage.

### Risiken
- **UI-Regression**: existing Operator-Verhalten (3 Tabellen sichtbar gleichzeitig) ändert sich. Empfehlung: Default-Tab auf "Aktuell", "Stabilität" als zweiter, "Baseline" als dritter — entspricht Reading-Order von oben nach unten heute.
- **A11y**: Tabs-Primitive muss Keyboard-Nav korrekt implementieren — die V-DB5-Tooltip-Lehre (Vorschlag 5) gilt analog.
- **Mobile**: Tab-Container kann auf schmalen Screens horizontal scrollen — Mobile-Test pflichtig.

### Priorität
**P3** — kosmetischer Win, nicht operativ kritisch. Mit V-DB5-P1-UI-Tranche heute ist die akute UX-Schmerzschwelle weg. V-DB6-Sprint nach Phase 5.

---

## Vorschlag 5 — Tooltip-Primitive (Headless-Pattern, A11y-konform)

### Vorschlag
Echte Tooltip-Komponente (Headless-Pattern à la Radix-UI) statt nativer `title=`-Attribute.

### Warum jetzt?
Heute setzt das Dashboard hover-tooltips über `title="..."`-Attribute. Native Title-Attribute haben:
- **kein Touch-Support** — auf Mobile/Tablet unsichtbar
- **kein Keyboard-Trigger** — A11y-Verstoß
- **kein Custom-Styling** — Browser-Native-Box, gegen KAI-Visual-System
- **schlechte Lesbarkeit** — verschwindet nach 5s, kein scroll-back

Mit der V-DB5-P1-UI-Tranche heute hat der Dashboard mehrere wichtige Tooltips bekommen (LiftUncertainBadge `TIER_LIFT_UNCERTAIN_TOOLTIP`, Wilson-Lower-Threshold-Marker auf ProgressBars, Source-Backend-Key-Hint, Cohort-Drift-Mosaik). Alle nutzen native title= — also bleibt A11y-Verstoß bestehen.

### Erwarteter Nutzen
- **A11y**: WCAG-Touch-/Keyboard-/Focus-Trigger für alle Tooltip-Inhalte.
- **Visual**: konsistentes KAI-Tooltip-Style (Bg-2, Border-Subtle, Mono-Code-für-Backend-Keys).
- **Touchpoint-Standardisierung**: subZero-Bar-Erklärung, Lift-unsicher-Marker, Window-Kacheln-Drilldown, Threshold-Marker — alle nutzen dasselbe Primitive.

### Datenquellen / Systeme
- Neue `web/src/components/ui/Tooltip.tsx` (Headless-Pattern)
- Optional: lib `@radix-ui/react-tooltip` (akzeptable Dep, A11y-fertig) ODER eigene Implementation
- Globale CSS-Tokens `kai.tokens.css` für Tooltip-Styles
- Bestehende Touchpoints: LiftUncertainBadge.tsx, ProgressBar Threshold-Markers in 3 Source-Panels, Source-Backend-Key-Hints

### Umsetzungsweg
1. **Decision**: Radix-UI-Dep akzeptabel oder Eigen-Build? Empfehlung: Radix (Reife, A11y, Tests, ~10kB gzipped). Operator-Sign-off.
2. Falls Radix: `npm i @radix-ui/react-tooltip`. Falls eigen: Floating-UI als Lib für Positioning.
3. `web/src/components/ui/Tooltip.tsx` als Wrapper mit KAI-Styling-Defaults.
4. Migration-Pass: alle `title="..."` im Dashboard durch `<Tooltip content="...">...</Tooltip>` ersetzen. Touchpoints (Anzahl ~12-15) systematisch durchgehen.
5. Tests: Touch-Trigger, Keyboard-Trigger, Focus-Trigger, Auto-Position.
6. Storybook-/Manual-Test-Liste pflegen.

### Parallel möglich?
**Ja**, eigenständiges Primitive. Konflikt mit Vorschlag 4 (3-Panel-Konsolidierung) nur bei Touchpoints, die Vorschlag 4 ohnehin neu schreibt.

### Aufwand
**Realistisch**: 4-6h Tooltip-Primitive + 4-6h Migration aller Touchpoints + 2h Tests + Deploy. Total ~1.5-2 Arbeitstage.

### Risiken
- **Bundle-Size**: Radix-Tooltip ~10kB gzipped. Akzeptabel.
- **Konsistenz**: Migration muss vollständig sein — nicht halb-migrierte Tooltip-Layer auf dem Dashboard. Operator-Cleanup-Pass nach Migration.
- **Mobile**: Tooltip-Trigger auf Mobile = long-press? Hover? Operator-Decision (Empfehlung: Radix-Standard).

### Priorität
**P3** — wichtig für A11y-Compliance, aber kein operativer Schmerz. Eigener Sprint nach Phase 5. V-DB5-P1 hat kein Tooltip-Primitive eingeführt — bewusste Entscheidung, Scope-Klein-Halten.

---

## Vorschlag 6 — Hold-Report-Snapshot Auto-Refresh

### Vorschlag
`kai-hold-report.timer` (existiert daily 05:00 UTC) muss `artifacts/ph5_hold/ph5_hold_metrics_report.json` aktualisieren ODER der Snapshot wird mit `generated_at`-Warning annotiert wenn >6h alt.

### Warum jetzt?
Operator-Befund 2026-05-08: Snapshot ist 16+ Tage alt. Live-API baut Report on-demand mit 30s-Cache, aber Disk-Snapshot ist stale. Wenn Operator den Snapshot zur Diagnose liest (Telegram-Audit-Reply, manueller `cat`), sieht er alte Felder ohne `per_source_active_precision`/`per_source_stability`. Das ist genau der V-DB5-Inkonsistenz-Trigger: visualisierte UI ≠ disk-snapshot.

### Erwarteter Nutzen
- **Diagnose-Verlässlichkeit**: ein Operator-`cat artifacts/ph5_hold/ph5_hold_metrics_report.json` zeigt aktuellen State, nicht 16d-alte-Form.
- **Audit-Verlässlichkeit**: post-mortem-Auswertungen lesen aus dem Snapshot (z.B. nach Pi-Crash mit Live-API-Down), bekommen aktuelle Daten.
- **Schema-Konsistenz**: Snapshot enthält gleiche Felder wie Live-API.

### Datenquellen / Systeme
- `deploy/systemd/kai-hold-report.service` + `kai-hold-report.timer`
- `app/cli/main.py` `kai cli alerts hold-report`-Command
- `artifacts/ph5_hold/ph5_hold_metrics_report.json`
- `app/api/routers/dashboard.py` hold-metrics-Endpoint (Cache 30s)

### Umsetzungsweg
**Option A (empfohlen)**: Timer triggert Hold-Report-CLI nightly, Snapshot wird neu geschrieben. Backup vor Overwrite. Lock-File während Schreiben.

**Option B**: Snapshot-Read fügt `generated_at`-Diff-Warning ein, wenn >6h alt. Ergänzt Option A für Crash-Resilience.

1. systemd-Timer-Audit: `kai-hold-report.timer` Status auf Pi 5 prüfen — läuft er? Wann zuletzt? Failed?
2. Falls Timer-Failed-Run: Root-Cause (vermutlich Pfad-Drift `/home/ubuntu/...` vs `/home/kai/...`).
3. CLI-Command-Audit: `kai cli alerts hold-report --output-file <path>` — schreibt korrekt?
4. **Option B implementieren** als Failsafe: in `app/api/routers/dashboard.py` Snapshot-Read mit `generated_at`-Check, bei stale: Header `X-KAI-Stale-Snapshot: true` + Field `_warning: "Snapshot is N hours old, live API recommended"`.
5. **Option A nachziehen**: Timer-Reparatur, Smoke.
6. Tests: Snapshot-Read mit altem Datum gibt Warning, Snapshot-Write Backup-Pfad existiert.

### Parallel möglich?
**Ja**, eigenständig. Konflikt nur mit Phase-1-Evidence-Schema-Migration (wenn Hold-Report neue Felder bekommt — dann Migration-Plan synchronisieren).

### Aufwand
**Minimal**: 1h Timer-Audit + 2h Option-B-Failsafe + 1h Option-A-Repair + 1h Tests + Deploy. Total ~halber Arbeitstag.

### Risiken
- **Operativ**: Falls Timer-Failed-Status länger als 16d unbemerkt, gibt es vermutlich auch andere Timer-Issues (kai-watchdog, kai-tg-listener-Heartbeat-Timer). Vorschlag 6 inspiziert *einen* Timer — eigener Pass für alle 6+ KAI-Timer empfehlenswert.
- **Schema-Drift**: Snapshot-Schema und Live-API-Schema müssen synchron bleiben. Test-Pflicht: Snapshot-Form == Live-API-Form (modulo `generated_at`).

### Priorität
**P2** — operativer Schmerz akut, aber nicht-blockierend (Live-API funktioniert). Mit Forward-Precision-Watchdog (Vorschlag 2) zusammen ausführen, weil ähnliches Watchdog-Pattern.

---

## Zusammenfassende Empfehlung & Priorisierung

| # | Item | Priorität | Aufwand | Empfehlung |
|---|---|---|---|---|
| 1 | Sticky-Highwater Re-Entry-Gate | P2 | 1d | Nach Phase-1-Rescue, vor Phase-3 |
| 2 | Forward-Precision Watchdog | **P1** (höher als V-DB5-Memo) | 0.5d | **Sofort nach Rescue-Commit** |
| 3 | Domain-Hash vs Storage-Hash Drift | P3 | 1d | **Koppeln an Phase-1-Evidence-Schema** |
| 4 | 3-Source-Panels konsolidieren | P3 | 1.5d | **V-DB6-Sprint, nach Phase 5** |
| 5 | Tooltip-Primitive | P3 | 1.5-2d | **Eigener Sprint nach Phase 5** |
| 6 | Hold-Report-Snapshot Auto-Refresh | P2 | 0.5d | **Mit Vorschlag 2 zusammen** |

**Erste Tranche-Empfehlung (1-2 Tage):** Vorschläge **2 + 6** (Watchdog + Snapshot-Refresh). Beide P2/P1, beide ~halber Arbeitstag, beide silent-failure-Mode-Mitigation.

**Zweite Tranche (1 Woche, koppelt an Phase 1):** **3** (Domain-Hash) zusammen mit Evidence-Schema-Live.

**Dritte Tranche (Phase 5+):** **1 + 4 + 5** (Re-Entry-Gate + 3-Panel-Konsolidierung + Tooltip-Primitive) als V-DB6-Sprint.

---

## Operator-Action gefordert

Bitte sign-off pro Vorschlag (oder Block-/Re-Prio-Begründung). Ohne Sign-off bleibt Item im V-DB5-P2-Backlog. Empfohlen: parallel zur Phase-1 (V-DB5-Backend-Rescue) starten, weil unabhängige Module.

**Cross-Refs:**
- `kai_master_audit_20260509.md` Phase-Einordnung
- Memory `v_db5_audit_backlog_20260508.md` — Original-Audit-Notes
- Memory `v_db5_backend_uncommitted_risk.md` — Phase-1-Rescue-Pflicht
