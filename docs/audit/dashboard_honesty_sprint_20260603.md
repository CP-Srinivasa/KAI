# Dashboard-Honesty-Sprint — Abschlussbericht (2026-06-03)

**Goal:** Alle Dashboard-Karten mit Skeleton/Planning/Beta/„Integration ausstehend"/
Phase-/Prozent-Texten auflösen — jede Karte **funktional + getestet** ODER ehrlich
als **Paper-Mode / Live-only / Roadmap / Keine Daten / Backend nicht erreichbar**.

**Geliefert:** PR #142 (Karten + Live-Tiles) + #143 (vitest + 46 Tests), beide in
p7 (`376bd4c3`). Kein Backend-/Trading-/Runtime-Change; `entry_mode=disabled`.

## Architektur-Kernentscheidung

`PreparedPanel` rendert **keinen** `DevelopmentStatus`-Prozent/Phase-Strip mehr,
sondern ein **ehrliches Status-Badge** (`PreparedStatus`: roadmap / paper_only /
live_only / no_data / unavailable). Das entfernt alle irreführenden %/Phase-Texte
seitenübergreifend in einer Komponente. Legacy-Props `phase`/`progress` werden
akzeptiert, aber nicht gerendert; `timeline` → ehrliche Roadmap-Notiz ohne %.

## Statusmatrix (Karten)

| Karte | Seite | Status | Quelle |
|---|---|---|---|
| Portfolio Snapshot | Dashboard | **FUNKTIONAL** (Paper) | `GET /operator/portfolio-snapshot` |
| Risk Meter | Dashboard | **FUNKTIONAL** (Paper) | `GET /operator/exposure-summary` |
| Allocation (Balken) | Dashboard | **FUNKTIONAL** (Paper) | aus portfolio-snapshot abgeleitet |
| Recent Trading Cycles | Dashboard | **FUNKTIONAL** (Paper) + Fehlerkarte | `GET /operator/trading-loop/recent-cycles` |
| Kapital: Börse·Konto·Ausgezahlt | Portfolio | **Live-only** (Paper n/a) | Balance-Reader/Margin/Withdrawal-Audit (Live) |
| Equity / PnL Kurve | Dashboard/Portfolio | **Roadmap** | Aggregation aus `paper_execution_audit.jsonl` |
| Sentiment Stream | Dashboard | **Roadmap** | `GET /operator/recent-news` |
| AI Insights (Kurzkarte) | Dashboard | **Roadmap** | stabiler Insight-Endpoint |
| Marktübersicht | Markets | **Roadmap** | `GET /markets/overview` |
| Asset-Detail | Markets | **Roadmap** | Operator-Signals-Endpoint + Markets-Mapping |
| Live-News-Stream | News | **Roadmap** | `GET /operator/recent-news` |
| News-Detail (Drawer) | News | **Roadmap** | recent-news + Signal-Linking |
| Marktrisiko 7/30d | Risk | **Roadmap** | `GET /operator/risk-summary` |
| Verpasste Trading-Chancen | Risk | **Roadmap** | `blocked_alerts.jsonl × alert_outcomes.jsonl` |
| Historischer Replay | Backtesting | **Roadmap** | `POST /operator/backtest/replay` |
| Strategie-Bibliothek | Backtesting | **Roadmap** (read-only, kein Toggle) | Backtest-Endpoint |
| Signal-Detailansicht | Signals | **Roadmap** | `GET /operator/signals/{id}` |
| Signal-Filter/History | Signals | **Roadmap** | historische Signal-Query (`/query/validate` wiederverwenden) |
| Modell-Observationen/Explain | AI Insights | **Roadmap** | `GET /operator/signals/{id}/explain` |
| Feature-Drift/Kalibrierung | AI Insights | **Roadmap** | `ph5_hold_metrics_report.json + alert_outcomes.jsonl` |
| Alerts-Versand-Diagnose | Alerts | **Roadmap** | `AlertAuditRecord` um delivery_status/http_status/error_reason/retry_count erweitern |

**Keine** Karte trägt noch Skeleton-/Planning-/Beta-/Prozent-/Phase-Texte.

## Endpoint-Inventar

**Vorhanden + jetzt verdrahtet:** `/operator/portfolio-snapshot`,
`/operator/exposure-summary`, `/operator/trading-loop/recent-cycles`.

**Fehlend → ehrlich als Roadmap markiert (nicht gebaut, Acceptance-konform):**
`/operator/risk-summary`, `/operator/recent-news`, `/markets/overview`,
`/operator/signals/{id}`, `/operator/signals/{id}/explain`, historische
Signal-Query, `POST /operator/backtest/replay`, Strategie-Bibliothek-Read,
Equity/PnL-Aggregation, Alerts-Versand-Diagnose-Felder.

## UI-Änderungen

- **NEU** `web/src/components/panels/LivePortfolioTiles.tsx` — 4 Live-Tiles, dep-free
  Allocation-Balken, ehrliche loading/empty/error-States, `fmtUsd`+`computeAllocation` exportiert.
- **GEÄNDERT** `PreparedPanel.tsx` — Status-Badge statt %/Phase-Strip.
- **GEÄNDERT** `Dashboard.tsx` — Live-Tiles verdrahtet, PREPARED_PANELS auf echte Roadmap-Items reduziert, „Integration ausstehend"→„Roadmap".
- **GEÄNDERT** Backtesting/Markets/News/Risk/Portfolio — `phase`/`progress`/`timeline` → `status`/`roadmapNote`; Portfolio-Kapital-Karte → `live_only`.

## Gelöschte sichere Altlasten

- `web/src/components/system/DevelopmentStatus.tsx` — nach PreparedPanel-Refactor vollständig unreferenziert (dead code). Keine Audit-JSONL/Migration/Risk-Gate/Key-Material berührt.

## Tests

- **NEU** vitest + @testing-library/react + happy-dom (jsdom@27 hatte `@asamuzakjp/css-color` ERR_REQUIRE_ESM).
- `PreparedPanel.test.tsx` (5): Status→Badge, kein %-Rendering, roadmapNote.
- `LivePortfolioTiles.pure.test.ts`: `fmtUsd` + `computeAllocation` (leer/unpriced/sortiert/Shorts).
- `LivePortfolioTiles.test.tsx`: gemockte API → echte Equity + Flat-Book-Empty-States + „Backend nicht erreichbar".
- **`npx vitest run` → 46 passed (6 Files)**; **`npm run build` (tsc -b + vite) → grün**.
- Backend: kein Change → Backend-Tests nicht betroffen. CI führt vitest noch nicht aus (Folgeschritt: vitest-Step in ci.yml).

## Echte Roadmap-Punkte (priorisiert)

1. Read-Endpoints: Equity/PnL-Aggregation, `/operator/risk-summary`, `/operator/recent-news`, `/markets/overview`.
2. Hebel `GET /operator/signals/{id}` (schaltet Signals + Risk + AI-Insights) + `/explain`.
3. `POST /operator/backtest/replay` (deterministisch) + read-only Strategie-Bibliothek.
4. Alerts-Versand-Diagnose (delivery_status/http_status/error_reason/retry_count) migrationssicher.
5. Verpasste-Chancen-Join + Feature-Drift-Aggregation.
6. `vitest`-Step in `ci.yml`; Drawer-Tests sobald Endpoints stehen.

## Safety

Read-only UI. Kein Live-Trading, `EXECUTION_ENTRY_MODE=disabled`, Risk-Gates `audit`
unberührt. Keine Secrets, Audit/FSM/HMAC/Replay/Migrationen nicht angefasst. Kein
Frontend-Deploy in diesem Sprint (separate koordinierte Aktion wegen
Parallel-Frontend-Arbeit — `pi_deploy_web.sh` überschreibt sonst).
