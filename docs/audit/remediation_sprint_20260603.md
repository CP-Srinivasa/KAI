# Remediation-/Hardening-/Cleanup-Sprint — Statusmatrix (2026-06-03)

**Auftrag:** Operator-`/goal` an Architect/Neo/Satoshi/SENTR/DALI — alle Findings
aus drei Audit-Sessions gegen aktuellen HEAD prüfen, beheben, sichere Altlasten
räumen, Zukunftsfähigkeit vorbereiten. Kein Schein-Done.

## Ground-Truth (verifiziert)

| Fakt | Wert |
|---|---|
| HEAD | `neo/p001-signal-source-attribution` @ `d670e152` (PR #132 OPEN) |
| Trunk p7 | `claude/p7/reentry-ia-codex-cycle` @ `ea3355b0` (#131) |
| HEAD vs p7 | **1 ahead / 1 behind** (Neo-Attribution voraus, #131 daily-report fehlt) |
| Working tree | sauber (vor Sprint) |
| Offene PRs | #132 (Neo-Attribution), #129 (logrotate, DRAFT), #100 (MATIC-Korrektur), #95 (bearish block-volume) |
| Offene Paper-Position | **DOT/USDT, qty 810.31, entry 1.2106, akt. 1.12 → −7.48 % / −73.42 USD unrealisiert** |
| Shadow-Ledger (lokal) | leer (resolved=0, `INSUFFICIENT_DATA`) |
| entry_mode (lokal/Default) | `paper`; **Pi-Soll laut Operator: `disabled`** |

## Betriebswahrheit (bestätigt, unverändert)

Operative **Phase 2 ist NICHT freigegeben**. SHADOW_ONLY-Flip, Priority-Scoring
und operative Phase-2 bleiben vertagt. `EXECUTION_ENTRY_MODE=disabled` bleibt
aktiv bis Edge/Safety geklärt. Dashboard-„Phase 2" ist reine UI-Roadmap und wurde
in dieser Session entkoppelt (siehe Dashboard). README sagt bereits
„Paper-First, Live-Execution disabled". DECISION_LOG/CHANGELOG dokumentieren den
EntryMode-Kill-Switch korrekt.

## Pre-Re-Enable-Blocker

| # | Blocker | Status | Evidenz / Begründung |
|---|---|---|---|
| 1 | 04.06.-Shadow-Report auswerten | **HART-BLOCKIERT (zeit-/datengated)** | Tooling fertig: `scripts/shadow_report_oneshot.sh` (read-only) + CLI `trading shadow-resolve`/`shadow-report` (#130). T+48h-Fenster schließt erst **2026-06-04**; lokaler Ledger leer. Caveat ist im Script-Zweck verankert (nur Exit-/Regime-Geometrie, keine degenerierte Confidence). Auswertung frühestens 2026-06-04. |
| 2 | Pi-Drift schließen | **DOKUMENTIERT, Deploy offen** | HEAD 1 ahead/1 behind p7 (#132 noch nicht gemerged, #131 fehlt lokal). **Kein Deploy in dieser Session** (Regel: kein riskanter Deploy ohne Dry-Run/Rollback). Pi-Live-Stand braucht separaten SSH-Check (`ubuntu@192.168.178.23`). |
| 3 | Offene Positionen beobachtbar | **ERLEDIGT** | Neu: `app/observability/position_risk.py` (reine Klassifikation) + CLI `trading positions-risk-snapshot` → Artefakt `artifacts/open_positions_snapshot.json` mit symbol/side/size/entry/current/unrealized-PnL/risk_status/source/age/mode. Risk-Status: `no_risk`/`risk_open`/`data_unknown`. 10 Unit-Tests grün, ruff+mypy clean. Live verifiziert: DOT/USDT → `risk_open`, −73.42 USD. |
| 4 | Bleed-/Loss-Circuit-Breaker | **TEIL-ERLEDIGT (Detektion) / Enforcement vorbereitet** | Detektions-Hälfte gebaut: `risk_open`/`data_unknown` + lautes WARN, read-only, fail-safe (kein Eingriff in Entry-Pfad). **Enforcing-Hälfte spezifiziert**: Gate, das vor Entry-Mode-Promotion `overall_risk_status != no_risk` → `manual_review`/block erzwingt (SENTR fail-closed). Aktuell nicht akut, da `entry_mode=disabled` alle Entries bereits blockt. |
| 5 | Nightly `edge_report.json` persistieren | **TEIL-ERLEDIGT (Persistenz+Bugfix), Pi-Timer offen** | Neu: `scripts/edge_report_oneshot.sh` (read-only Wrapper, stable + dated artifact, Operator-Ping). `artifacts/edge_report.json` lokal erzeugt + als valides JSON verifiziert (13 keys, 41 closed, notes/caveats, excluded_quarantined). **Dabei Bug gefunden+gefixt** (siehe Finding J1): `--json`-Ausgaben emittierten via rich-Console → Zeilenumbruch korrumpierte langes JSON. Offen: systemd-Timer-Install auf Pi. |
| 6 | Echten Signal-Edge neu messen | **HART-BLOCKIERT (daten-/methodisch), Werkzeug vorbereitet** | Alter negativer Edge (`P(mu_net>0)=0%`) war **Canary-Probe-Artefakt** (hardcoded bullish, siehe Memory `kai_edge_verdict_canary_probe_artifact`). PR #132 (NEO-P-001) liefert signal-source-attribution auf fills/closes + `edge_report by_source` → trennt Canary von echtem SignalGenerator-Edge. Echte Messung braucht FORWARD-Daten mit Attribution → erst ab #132-Merge + Laufzeit. |

## Dashboard

| Punkt | Status | Detail |
|---|---|---|
| Stub-Karten von „operativer Phase 2" entkoppeln | **ERLEDIGT** | 10 `PreparedPanel`-Karten: `"Phase 2 …"` → `"Dashboard-Roadmap …"` (Dashboard, Markets, Risk, Backtesting, Portfolio, News, Signals, Settings, Alerts, AIInsights). Badge „Integration ausstehend" war bereits vorhanden. **KaiLiveWidget-„Phase 2" (Chat-Feature) bewusst unberührt.** Frontend-Build exit 0. |
| Quick-Win-Tiles anbinden | **VORBEREITET** | Backend existiert: `GET /operator/portfolio-snapshot` (Portfolio Snapshot, Allocation), `GET /operator/exposure-summary` (Risk Meter); AI-Insights-Page existiert. Nur Dashboard-Tile-Wiring fehlt — reine Frontend-Aufgabe, kein neuer Endpoint. |
| Fehlende echte Endpoints | **ROADMAP markiert** | Equity/PnL, Sentiment-Stream, Signal-Detail, Signal-History, Backtest-Replay, Secret-Vault-Test, Alerts-HTTP-Status, Markets-Overview. **Hebel:** `GET /operator/signals/{id}` schaltet 3 Stubs (Signals+Risk+AIInsights), Backtest-Replay-Endpoint schaltet 2 (Backtesting+Risk). |

## Weitere Audit-Punkte

| Punkt | Status | Detail |
|---|---|---|
| AGENTS/Roster konsolidieren | **OFFEN** | Doc-Roster vs API-Agenten vs Worker-Handler vs Personas nicht in dieser Session abgeglichen — braucht eigenen Durchgang (Architect). |
| mypy-Burn-down Execution/Bridge/Loop/paper_engine | **WIDERLEGT für Priofeld** | `mypy app/execution/ app/orchestrator/trading_loop.py` = **0 Fehler / 30 Files**. Das priorisierte Feld ist bereits grün; Restschuld (Memory: ~20) liegt anderswo → Burn-down dort ansetzen (separate Messung). |
| JSONL-Rotation/Retention | **VORBEREITET** | Große Files: `api_request_audit.jsonl` 9.9M, `trading_loop_audit.jsonl` 5.6M, `alert_outcomes.jsonl` 4.2M, `alert_audit.jsonl` 2.1M. PR #129 (logrotate, DRAFT) adressiert genau das → reviewen/mergen + Retention-Policy. Audit-JSONL mit Hash-Chain **nicht** löschen. |
| SSRF/KYT/Exchange-Signing/Audit-Kette | **NICHT AUDITIERT** | In dieser Session nicht geprüft — ehrlich offen, braucht SATOSHI/SENTR-Tiefenreview. |
| **Finding J1** — `--json`-Korruption (Daten-Qualität) | **ERLEDIGT** | 8 CLI-`--json`/JSON-Commands in `trading.py` emittierten via `console.print(_json.dumps(...))` (rich Console). Rich bricht lange Strings auf Terminalbreite um → echte Newlines im JSON-Stream → unparsebare Artefakte (Nightly-edge/portfolio/edge-gate/etc.). Fix: builtin `print()` (ensure_ascii → encoding-sicher). Verifiziert: edge-report + portfolio-snapshot parsen jetzt. |
| Deploy-/Docker-/Pi-Doku angleichen | **OFFEN** | Nicht bearbeitet. |
| Persona-/Asset-Duplikate | **OFFEN (Sign-off nötig)** | Source-of-truth-Entscheidung erforderlich — nicht autonom bereinigbar. |
| Windows/CRLF/Path-DX | **TEILS (niedrig)** | `core.autocrlf=input` historisch gesetzt (Memory); kein Prod-Pfad-Risiko bearbeitet. |

## Cleanup (durchgeführt)

| Aktion | Begründung |
|---|---|
| Lokale Branches `sprint/lock-file-migration`, `sprint/lock-file-migration-v2`, `docs/lock-file-migration-sprint-spec` gelöscht | PR #85 + #86 **MERGED** → Squash-Merge-Leichen, kein Worktree gebunden, reflog-recoverable, origin behält sie. Enforcement (`app/ingestion/telegram_session_lock.py`) ist LIVE. |
| **NICHT** gelöscht | `__pycache__`/Caches (Parallel-Session-Risiko, geringer Wert), `artifacts/*.bak` (= Audit-Daten), Audit-JSONL/monitor/Migrationen/Risk-Gates/Key-Material (verboten laut Auftrag). |

## Geänderte / neue Dateien

- **NEU** `app/observability/position_risk.py` — reine Risk-Klassifikation (Blocker #3/#4).
- **NEU** `tests/unit/test_position_risk.py` — 10 Unit-Tests.
- **NEU** `scripts/edge_report_oneshot.sh` — Nightly edge-report-Persist-Wrapper (Blocker #5).
- **NEU** `docs/audit/remediation_sprint_20260603.md` — diese Statusmatrix.
- **GEÄNDERT** `app/cli/commands/trading.py` — Command `positions-risk-snapshot` + J1-Fix (8× `console.print`→`print` für JSON).
- **GEÄNDERT** 10× `web/src/pages/*.tsx` — Dashboard-Roadmap-Relabel.
- **GENERIERT** `artifacts/open_positions_snapshot.json`, `artifacts/edge_report.json` — Live-Artefakte.
- **GELÖSCHT (lokal)** 3 lock-file-Branches (gemerged).

## Tests / Builds

| Check | Ergebnis |
|---|---|
| Frontend `npm run build` | **exit 0** (validiert Relabel) |
| `ruff check` (touched) | **All checks passed** |
| `mypy app/observability/position_risk.py` | **Success** |
| `mypy app/execution/ trading_loop.py` | **Success, 0/30** |
| `pytest tests/unit/test_position_risk.py` | **10 passed** |
| `edge-report --json` → `json.load` | **valid** (war vor J1-Fix unparsebar) |
| `paper-portfolio-snapshot` → `json.load` | **valid** |
| `bash -n scripts/edge_report_oneshot.sh` | **syntax OK** |
| `pytest -k "edge_report or edge_release or cli or positions_risk"` | **146 passed, 0 failed** (J1-Fix ohne Regression) |
| Full pytest-Suite (4212 Tests) | **NICHT vollständig gelaufen** (Umfang/Zeit) — gezielte Pfade grün. |

## Verbleibende Risiken

1. **Offene DOT/USDT-Position blutet (−7.5 %)** ohne aktives Enforcement-Breaker; nur Detektion+WARN. Entry-disabled schützt vor Neueinstieg, nicht vor weiterem Drawdown der bestehenden Position (Exit-/Stop-Pfad ist davon unberührt aktiv).
2. **Pi-Drift nicht geschlossen** — Live-Stand ungeprüft; #132 noch OPEN.
3. **Echter Edge weiter unbekannt** bis #132-Attribution Forward-Daten liefert.
4. Security-Tiefenreview (SSRF/KYT/Signing) **ausstehend**.

## Re-Enable-Empfehlung

**NICHT re-enablen. `EXECUTION_ENTRY_MODE=disabled` bleibt.** Begründung:
offene blutende Position ohne Enforcement-Breaker (#4 nur Detektion), echter
Signal-Edge unmessbar bis #132-Forward-Daten (#6), Shadow-Report erst
2026-06-04 auswertbar (#1). Keine der drei Edge-/Safety-Vorbedingungen ist erfüllt.

## Nächste Sprints (priorisiert)

- **P0** Enforcing-Hälfte Bleed-Breaker (#4): Pre-Promotion-Gate `risk_open → manual_review`, SENTR fail-closed.
- **P0** #132 mergen → p7, dann Forward-Attribution sammeln (Voraussetzung #6).
- **P1** Nightly `edge_report.json`-Wrapper + Pi-Timer (#5).
- **P1** Shadow-Report 2026-06-04 auswerten (#1).
- **P1** JSONL-Retention: PR #129 reviewen/mergen.
- **P2** Quick-Win-Dashboard-Tiles (Portfolio/Risk/Allocation/AI-Insights) an bestehende Endpoints.
- **P2** `GET /operator/signals/{id}` (Hebel: 3 Stubs) + Backtest-Replay (2 Stubs).
- **P2** AGENTS/Roster-Konsolidierung, SSRF/KYT/Signing-Review, Deploy/Docker/Pi-Doku.
