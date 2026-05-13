# DALI Dashboard v2 — Master-Spec

**Branch:** `claude/dali-dashboard-v2-20260513`
**Worktree:** `C:/Users/sasch/.local/bin/kai-dali-dashboard-v2`
**Basis:** `origin/claude/p7/reentry-ia-codex-cycle` @ `1d28e6d` (post PR #16)
**Auftrag:** Operator-Brief 2026-05-13 — "Erweiterte UI/UX Optimierung & Strukturverbesserung"

---

## 0. Identitäts-Anker — NICHT VERHANDELBAR

Bestehender KAI-Look bleibt vollständig erhalten:

- **80er Neon / Cyberpunk / Synthwave / Holografisch** als Visual-System
- Tokens aus `web/src/index.css` + `kai.tokens.css` (`--pos`, `--neg`, `--warn`, `--info`, `--ai`, `--fg-subtle` etc.)
- Bestehende Klassen: `synthwave-pulse-edge`, `glow-pos/neg/warn/info/ai`, `attention-breathe-*`, `kai-pacman-march`, `currency-pellet`
- KEINE neuen Magic-Hex-Farben
- KEINE neuen npm-Dependencies
- KEINE Komplett-Redesigns

Der Auftrag ist **Klärung und Verdichtung**, nicht Neudesign.

---

## 1. Globale Regeln (gelten für jede Tranche)

### G1 Verständlichkeit > Techniksprache
- Keine Backend-Begriffe (`status_counts`, `idempotency_key` etc.) im sichtbaren UI
- Operator-Sprache: ganze Sätze, klare Bedeutung, deutsch
- Tooltipps für unvermeidbare Kürzel
- Microcopy-Tabelle pro Modul-Tranche

### G2 Erkennung auf einen Blick
- Jeder Card-Header beantwortet: *Was passiert hier?*
- Status-Badge mit Ton (`pos/neg/warn/info/ai`) + Klartext-Wort doppelt zu Icon/Farbe
- Hero-KPIs bekommen Klartext-Headline (siehe Trades.tsx Systemstatus-Headline-Regelwerk als Referenz)

### G3 Unfertige Features sichtbar markieren
Neue Komponente `<DevelopmentStatus />` mit:
- Phase (Planung / Skeleton / Beta / Stable)
- Fortschritts-Indikator (Synthwave-Progress-Bar, kein neues lib)
- Timeline (optional, "geplant für Sprint X")
- Klartext-Erklärung

Platzierung: Card-Header rechts oder als Top-Strip in der Card.

### G4 Regenbogenlinien INTEGRIEREN, nicht schweben
- `<PageHeader divider={false}>` pflicht für alle 9 Module
- Erste Inhalts-Card bekommt `synthwave-pulse-edge` Top-Border
- KEIN doppelter Glow (PageHeader-Linie + Card-Edge)
- Pattern bereits etabliert in Trades.tsx, ExternalSignals.tsx, Signals.tsx — auf alle anderen replizieren

### G5 Status-Spiegel (Live vs Simuliert vs Vorbereitet)
Drei Badge-Modi mit konsistenten Tönen:
- `Live` → `info` Ton, Pulsing-Dot
- `Paper / Simuliert` → `warn` Ton, statisch
- `Vorbereitet / Mock` → `fg-subtle` Ton, gestreift
- `Geplant` → `ai` Ton, Strichlinie

---

## 2. Audit-Befunde (vor Patch-Phase)

### A1 Regenbogenlinien-Drift
PageHeader-Default ist `divider=true`. Pages **mit** integrierter Linie (`divider={false}`):
- Trades.tsx (T1, 2026-05-12) ✅
- ExternalSignals.tsx (T7, 2026-05-11) ✅
- Signals.tsx (T6, 2026-05-11) ✅

Pages **mit schwebender Linie** (zu fixen):
- Dashboard.tsx, Portfolio.tsx, Alerts.tsx, Risk.tsx, Agents.tsx, Markets.tsx, News.tsx, Backtesting.tsx, AIInsightsPage.tsx, Settings.tsx — **10 Pages**

### A2 Agenten-Icons fehlen
Inventory `C:\Users\sasch\Desktop\` ergibt:

| Agent | PNG-Größe | Status |
|---|---|---|
| SENTR | 4.3 MB | vorhanden, optimieren nötig |
| Watchdog | 4.9 MB | vorhanden, optimieren nötig |
| Architect | 2.7 MB | vorhanden, optimieren nötig |
| DALI | 1.1 MB | vorhanden |
| Satoshi | 1.1 MB | vorhanden (neu) |
| Neo | 1.1 MB | vorhanden (neu) |

**Pipeline:** alle 6 PNG → resize 512×512, optimiertes PNG / WebP-Fallback, Ziel <150kB pro Asset. Ablage in `web/public/agents/{name}.png`.

### A3 Backend-Begriffe sichtbar
Stichprobe Signal-Matrix, Alert-Doc/P/Sentiment-Kürzel, "Realized PnL nach Asset" — viele Snake-Case-Strings, technische Codes ohne Tooltipps.

---

## 3. Modul-Tranchen-Plan (8 Module → 12 Tranchen)

| # | Modul | Tranche | Kern-Änderungen | Aufwand |
|---|---|---|---|---|
| **M1** | Dashboard-Übersicht | M1a Quality-Bar | Mehr Info, Status-Spiegel, Headline-Klartext | S |
| | | M1b Signal-Matrix | Grid-Alignment, Hierarchie, ein Status pro Zeile | M |
| | | M1c Directional-Alerts | Spalten-Rename (Doc→Dokument, P→Priorität etc.), Tooltipps | S |
| | | M1d Vorbereitet-Strip | Dynamische statt statische Daten | S |
| **M2** | Portfolio | M2a Header-Fix | divider=false + erste Card pulse-edge | XS |
| | | M2b Buckets | On-Exchange / On-Account / Withdrawn — Klartext, Status-Spiegel | M |
| | | M2c Realized-PnL | Per-Asset-Card, DevelopmentStatus | M |
| | | M2d Equity-Kurve | Drawdown-Spiegel, Headline, Erklärung | M |
| **M3** | Alerts | M3a Header-Fix + Versand-Diagnose-Edge | XS |
| | | M3b Card-Klartext | Was wird überwacht, was triggert, Folge | M |
| **M4** | Risiko | M4a Header-Fix | XS |
| | | M4b Risk-Score & Volatilität | Handlungsempfehlung, Risk-Level-Badge | M |
| | | M4c Missed-Signal-Analyse → **"Verpasste Chancen"** | Rename + Erklärung pro Block | M |
| **M5** | Agenten | M5a Asset-Pipeline + Icons | 6 Agenten, einheitliches Rahmen-System | M |
| | | M5b Card-Microcopy | Aufgabe / Status / Aktivität / Letzte Aktion / Warnung | M |
| | | M5c Header-Fix + Edge auf Agent-Cards | XS |
| **M6** | Märkte | M6a Header-Fix + Marktübersicht-Edge | XS |
| | | M6b Microcopy + Asset-Detail | Trendrichtung, verknüpfte Signale, Risiko-Badge | M |
| **M7** | News & Sentiment | M7a Header-Fix + Live-News-Stream-Edge | XS |
| | | M7b Card-Klartext | Relevanz, Sentiment-Wort + Tone, Einfluss, Signal-Link | M |
| **M8** | Backtesting | M8a Header-Fix + Replay-Edge | XS |
| | | M8b Strategy-Library + Historical-Replay | DevelopmentStatus (vermutlich Beta), Performance-Klartext | L |

**Quer-Tranche Q1:** Globale Komponente `<DevelopmentStatus />` + `<StatusBadge />` (Live/Paper/Vorbereitet/Geplant) — wird in mehreren Modulen wiederverwendet. **Vor M1 bauen.**

**Quer-Tranche Q2:** Microcopy-Wörterbuch `i18n/strings.ts` Erweiterungen — pro Modul nachrücken.

---

## 4. Sprint-Reihenfolge (Vorschlag)

| Sprint | Inhalt | Zeit-Schätzung |
|---|---|---|
| **S1** | Q1 (DevelopmentStatus + StatusBadge) + M2a + M3a + M4a + M5c + M6a + M7a + M8a — **alle 7 Header-Fixes als XS-Tranche** | ~1 Tag |
| **S2** | M5 vollständig (Asset-Pipeline + Microcopy + Icons-Wiring) | ~1.5 Tage |
| **S3** | M1 vollständig (Dashboard-Übersicht — Quality-Bar, Signal-Matrix, Alerts, Vorbereitet) | ~1.5 Tage |
| **S4** | M2 vollständig (Portfolio Buckets, Realized-PnL, Equity) | ~1.5 Tage |
| **S5** | M4 vollständig (Risiko + Missed→Verpasste-Chancen) | ~1 Tag |
| **S6** | M3 + M7 (Alerts + News) | ~1 Tag |
| **S7** | M6 + M8 (Märkte + Backtesting) | ~1 Tag |

**Gesamt-Aufwand:** ~8–9 Arbeitstage bei sauberer Sprint-Trennung.

Jeder Sprint = eigener Squash-PR auf p7 → keine alt/neu-Design-Drift wie 2026-05-12.

---

## 5. PR-Strategie (Drift-Vermeidung)

1. **Pro Sprint ein PR**, Squash-Merge auf `claude/p7/reentry-ia-codex-cycle`
2. Vor jedem Sprint: `git fetch origin` + Rebase auf p7-HEAD
3. Branch-Naming: `claude/dali-dashboard-v2-sX-<thema>-20260513` (S1, S2, …)
4. Master-Branch `claude/dali-dashboard-v2-20260513` ist nur Sammler — nicht direkt mergen
5. Worktree-Isolation strikt — keine parallelen Pi-Deploys anderer Sessions ohne Operator-OK
6. Jeder PR-Body: Tranchen-Liste + Smoke-Test-Punch + Cross-Refs zur Master-Spec

---

## 6. Out-of-Scope

- Backend-Endpoints, Schema-Erweiterungen, neue API-Felder (z.B. Realized-PnL pro Asset benötigt evtl. Backend) → SEPARATE Spec
- Mobile-Komplett-Redesign (responsive-Layer wird best-effort aktualisiert)
- TradingView-Widget-Refactor
- Topbar / Sidebar / ModeSelector
- Identitäts-Drift (kein Material-UI, kein Shadcn-Replace)
- DALI-Subagent-Workflow (`artifacts/agents/dali/`) — eigenes Thema

---

## 7. Approval-Gate

**Vor Sprint S1 startet:** Operator approved diese Master-Spec.

Mögliche Reaktionen:
- ✅ Approve → S1 los
- 🔄 Reorder → Sprint-Reihenfolge ändern
- ✂️ Cut → Module aus dem Plan streichen
- ➕ Add → Modul-Wünsche ergänzen

Spec wird beim Sprint-Lauf nicht mehr verändert — Änderungen über Operator-Auftrag in neuer Tranche.

---

## 8. Tracking & Nachvollziehbarkeit

- Master-Spec (diese Datei) bleibt im Worktree und in p7 nach S1-Merge
- Jeder Sprint-PR referenziert diese Spec
- Memory-Eintrag `dali_dashboard_v2_master_track_20260513` führt Done/Open
- Pro Sprint kurzes Outcome-Memory (lessons-learned, neue Pattern)

Cross-Refs:
- Bestehende DALI-Memorys: `dali_patches_20260510_deployed`, `dali_trades_arcade_sprint_20260512`
- Pattern-Vorlage: `docs/ui/dali_trades_arcade_spec.md`
- Feedback-Pin: `feedback_no_trailing_recaps`, `feedback_pre_deploy_frontend_branch_merge`, `feedback_pi_deploy_dist_drift_check`
