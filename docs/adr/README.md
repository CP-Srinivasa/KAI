# ADR-Index (`docs/adr/`)

**Stand:** 2026-07-11 · Architecture Decision Records von KAI.

**Konvention:** fortlaufende Nummern; Historie wird nie umgeschrieben — Korrekturen/Präzisierungen kommen als `## Addendum` in die bestehende ADR oder als neue ADR, die die alte supersedet.

## Nummern-Hygiene (bekannt, bewusst NICHT rückwirkend geändert)

- **0006 ist doppelt vergeben** (`0006-fastlane-fail-closed-bypass-defaults.md` UND `0006-source-intake-exploration-grey-area.md`).
- **0008 wurde nie vergeben** (Lücke zwischen 0007 und 0009).

Bestehende Verweise (Memory, PRs, Commit-Messages, Docs) referenzieren diese Dateinamen — rückwirkende Umnummerierung würde die Verweis-Stabilität brechen. Bei Zitaten von „ADR 0006" den vollen Dateinamen nennen. Neue ADRs setzen bei der nächsten freien Nummer fort (**nächste: 0016**).

## Index

| ADR | Titel | Status |
|---|---|---|
| [0001](0001-tradingview-integration.md) | TradingView Integration | Accepted (2026-04-16, D-125) |
| [0002](0002-signal-consensus-experimental.md) | Signal-Consensus als `@experimental` markiert | Experimental (pausiert) |
| [0003](0003-duckdb-storage-pivot.md) | DuckDB als Analytical-Read-Layer + JSONL-WAL als Source-of-Truth | Accepted |
| [0004](0004-premium-signal-auto-fill.md) | Premium-Signal Auto-Fill (Paper-Mode) | Accepted (2026-05-12) |
| [0005](0005-premium-fastlane-30d.md) | Premium-Telegram Fastlane (30-Tage-Testfenster) | Accepted; Bypass-Default-Posture superseded durch ADR 0006 (fastlane) |
| [0006a](0006-fastlane-fail-closed-bypass-defaults.md) | Premium-Fastlane: fail-closed Bypass-Defaults + Entry-Mode-Override-Preflight | Accepted |
| [0006b](0006-source-intake-exploration-grey-area.md) | Source-Intake Exploration (Graubereich, isolierte Sandbox) | Accepted |
| [0007](0007-generator-path-no-edge.md) | Current generator/screener/news path = NO_EDGE | Accepted (2026-06-19) |
| — 0008 | *(nie vergeben)* | — |
| [0009](0009-freshness-timestamp-trust-tiers.md) | KORREKTUR: „RSS-Feed-Latenz" war ein `mktime`/Timezone-Parse-Artefakt | Accepted (2026-06-19) |
| [0010](0010-live-replay-dual-stream.md) | Live/Replay-Umschaltung + Shadow/Live-Vergleich | Phase 1 implementiert (default-off); Phase 2 GATED |
| [0011](0011-g0-demand-probe-preregistration.md) | G0 Demand-Probe: Pre-Registration | Accepted (2026-06-24) |
| [0012](0012-north-star-pivot-research-truth-platform.md) | NORTH_STAR-Pivot: von Alpha-Jagd zu Research-/Truth-Plattform (Hybrid) | **ACCEPTED (2026-06-29) + Addendum 2026-07-02** — aktueller Wegpunkt |
| [0013](0013-frontier-and-boundary.md) | Frontier & Boundary: souveräner Zugang statt Umgehung | ACCEPTED (2026-07-01) |
| [0014](0014-kai-protocol-zielbild.md) | KAI Protocol: Zielbild & Schichtenkarte (Verifiable AI Finance) | ACCEPTED (2026-07-06) |
| [0015](0015-kai-local-intelligence-layer.md) | KAI Local Intelligence Layer (lokales LLM als auditierbare Shadow-Schicht) | ACCEPTED (2026-07-11) |

**Hinweis:** „0006a/0006b" sind nur Index-Labels dieses Dokuments zur Unterscheidung — die Dateien selbst heißen beide `0006-*` und behalten ihre Namen.

## Leit-ADRs

- **ADR 0012** definiert den aktuellen Wegpunkt (Truth-/Falsifikations-Plattform) **innerhalb** der unveränderten Gesamt-Vision (`docs/KAI_IDENTITY.md`) — siehe Addendum (a).
- **ADR 0013** definiert die Zugangs-/Realisierungs-Achse (legale Frontier, Tier-Karte, Lizenz-Gate).
- **ADR 0014** definiert das Zielbild-Dach „KAI Protocol" (Schichtenkarte, Demand-Gates, Tier-2-STOP-Schilder, Design-Invarianten).
