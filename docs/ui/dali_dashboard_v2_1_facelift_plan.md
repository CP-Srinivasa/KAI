# KAI Control Center — Dashboard UI v2.1 „DALI meets Leonardo da Vinci"

**Stand:** 2026-09-15 · **Basis:** Mainline `claude/p7/reentry-ia-codex-cycle` @ `ff074d48` (Worktree `/c/tmp/kai-dash-v21`) · **Status:** Änderungsplan + Entwurf + Diff-Proposals, **noch nichts gemergt, nichts deployt**.
**Vorgängerin:** `docs/ui/dali_dashboard_v2_master_spec.md` (v2, 2026-05-13, S1–S7 gemergt) — die „DALI-Datei" des Sprint-Prompts.
**Proposals:** `artifacts/agents/dali/proposals.jsonl` (DALI, Modus `implement`), Findings in `artifacts/agents/dali/findings.jsonl`.
**Entwurf:** Design-Canvas „KAI Control Center v2.1" (Desktop 1440 / Mobile 390), Link im Abschlussbericht.

Leitsatz: **Ordnung vor Effekt.** Der Neon-Look bleibt (Cyan/Violett-Akzente, Schwarz/Anthrazit-Flächen), aber Bewegung, Glow und Dopplung werden auf das reduziert, was eine Bedeutung trägt.

---

## 0. Was heute wahr ist (belegt, Stand ff074d48)

Gemessen im Preview-Build (Vite 8, Dark-Mode, kein Backend erreichbar → alle Panels im Fehler-/Leerzustand; Layout-Befunde sind davon unabhängig).

| Befund | Beleg | Bewertung |
|---|---|---|
| **Command-Header scrollt weg.** `sticky top-0` in `components/layout/CommandHeader.tsx`, aber `<main class="overflow-x-hidden">` in `layout/AppShell.tsx` ist Scroll-Container → sticky wirkungslos. | Browser 1920 px: bei `scrollY=800` liegt der Header bei `top=-700`. | **P0** — die „nie wegscrollende Lage-Leiste" (WP-1.1) existiert de facto nicht. |
| **Horizontaler Überlauf** bei 320 px (Dokument 351 px) und 768 px (Dokument 1047 px). Ursache: rechter Topbar-Cluster (`ml-auto flex …`, Identitäts-Badge, Währung, Dichte, Sprache, Theme, Glocke) bricht nicht um; ab `md` nimmt die Sidebar 232 px, der Topbar bleiben 536 px. | Harness 320/768: `docScrollW` 351 / 1047. | **P0** — verstößt gegen „keine horizontal überlaufenden Seiten". |
| **Abgeschnittenes Bedienelement** bei 390 px: Datenbasis-Umschalter in `EdgeTruthPanel` reicht bis 441 px, `main` clippt ihn. | Harness 390: Element `right=441` bei `vw=390`. | **P0** — verdecktes Bedienelement. |
| **Touchflächen:** 39 von 39 sichtbaren Bedienelementen < 44 px in mindestens einer Dimension (Nav-Items `h-8`, Topbar-Buttons `h-8`/`h-7`). | Harness 320/390/768. | **P0** mobil. |
| **Mobile Navigation** nur als Hamburger-Drawer (`Sidebar.tsx`); keine 4 Hauptziele + „Mehr", keine `safe-area`-Insets, aria-labels englisch („Open navigation menu", „Toggle theme", „Close menu"), kein `focus-visible` in Sidebar/Topbar. | Code. | **P0/P1**. |
| **Dauer-Animationen:** 36 Elemente mit laufender CSS-Animation auf der Startseite (`synthwave-pulse-edge` 8 s endlos, `attention-breathe-*` auf jeder KPI unter Ziel, `animate-pulse` auf Live-Dots). `prefers-reduced-motion` wird respektiert. | Browser 1920 px. | **P1** — „keine dauernd pulsierenden Dekorationen". |
| **Report-Zeitpunkt 5×** (Seitenkopf, Command-Header, Quality-Bar-Fuß, Agent-Roster-Untertitel, Wahrheitsstatus-Diagnose); **Version/Build 5×** (Backend-Banner, Command-Header, Footer, Wahrheitsstatus-Diagnose, System-Seite). | Code `pages/Dashboard.tsx`, Panels. | **P1**. |
| **Handelsmodus ist ein Frontend-Schalter.** `AppState.mode` (paper/sim/live) liegt in `localStorage`, ohne Backend-Bindung; angezeigt in Sidebar-Pill, Topbar-ModeSelector und als grüne Linie in AppShell. Die echte Wahrheit sind `execution_enabled`/`write_back_allowed` (`/operator/status`, `/operator/portfolio-snapshot`) und `APP_PAYMENT_MODE`/`APP_LN_PAY_ENABLED`. | Code `state/AppState.tsx`, `trading/ModeSelector.tsx`. | **P0** — verstößt gegen „Backend-Verbindung, Datenalter, Handelsmodus getrennt darstellen; Live-Daten ≠ Trading-Freigabe". |
| **Priorität der Startansicht** heute: Command-Header → Titel+Report → Executive Snapshot (Kapital) → Akute Punkte → Premium-Banner → Fehlerkarte → Wahrheitsstatus → Edge-Wahrheit → Fokus-Toggle → n-Übersicht → 9 KPI-Karten → Re-Entry → Regime → Lightning (allein in 4/12) → Quality-Bar/Signal-Qualität/Trading-Loop → Precision → Reliability → Signal-Matrix/TradingView → Agenten → Timer → Alerts → Portfolio-Kacheln → Vorbereitet → Re-Entry-Historie → Footer. Seitenhöhe bei 320 px: 7039 px. | Code, Harness. | **P0** — Handlungsbedarf steht hinter Kapital; Betriebslage ist verteilt. |
| **„Was macht KAI gerade?"** ist ein Stub: `/api/kai/state` liefert `is_stub=true`, Header zeigt „KAI · Stub (P1)" bzw. „KAI · n/v". | `app/api/routers/kai.py:96`. | **P1** — ableitbar aus vorhandenen Daten (letzter Trading-Zyklus, Timer). |
| **Künstliche Kartenhöhen:** `lg:[&>*]:h-full` in 6 Grids und `lg:[&>*]:flex-1` im rechten Stapel. | `pages/Dashboard.tsx` 445–505. | **P1**. |
| **Agenten:** Backend (`app/api/routers/agents.py`) liefert `wiring: autonomous|interactive` (3 autonom: SENTR, Watchdog, Architect; 8 interaktiv) — Frontend-Typ `AgentSummary` und beide Karten ignorieren es; Untertitel der Agenten-Seite „alle ausschließlich von Claude Code ausgeführt" ist für die drei autonomen falsch. Icons: 6 PNG (159–288 kB, Spec-Ziel <150 kB) + 5 Inline-SVG → uneinheitlich. Status-Wörter „live/prepared/offline" englisch. Originalgrafiken für Einstein, KAI-Finder, Xqu liegen unter `C:\Users\sasch\Desktop\<Name>\<Name>.png`; für Architecture Red Team und Data Quality Inspector gibt es keine. | Code, Desktop-Inventar. | **P1**. |
| **Directional Alerts „P":** `priority` ist `int | None` 1–10 (`app/alerts/audit.py:68`); Bänder `app/alerts/formatters.py`: 1–3 Low, 4–6 Medium, 7–8 High, 9–10 Critical; High-Conviction ab 10; Alert-Gate filtert P<7. Dashboard-Karte hat bereits „Dokument/Stimmung/Priorität/Assets" (v2 S3). Tooltip „P≥7 = Premium-Signal" korrekt, Bänder fehlen. | Code. | **erledigt/ergänzen**. |
| **Signal-Matrix:** `min-w-[520px]`-Grid in `overflow-x-auto` → verschachtelte Scrollfläche mobil; keine Listenansicht. | `panels/SignalHeatmap.tsx:257`. | **P1**. |
| **Quality-Bar:** Klartext-Verdict vorhanden (v2 S3); Bedeutung und Handlungsbedarf fehlen. | `panels/QualityBar.tsx`. | **P1**. |
| **Vorbereitet-Sektion:** 3 Roadmap-Karten (Equity/PnL, Sentiment Stream, AI Insights) ohne „Termin offen"; AI-Insights-Karte verweist nicht auf die vorhandene Route `ai`. | `pages/Dashboard.tsx` PREPARED_PANELS. | **P1**. |
| **Doppelte Abrufe:** `fetchLightningStatus` in `LightningPanel` + `NodeStatusKpi`; `fetchDiversificationOverview` in `ExecutiveSnapshot` + `DiversificationPanel`; TradingView-iframe lädt sofort, wenn `VITE_TRADINGVIEW_ENABLED`. Portfolio-Snapshot ist bereits geteilt (2026-09-09). | Code. | **P2**. |
| **Speicher-Feedback:** kein app-weites „Wird gespeichert → Gespeichert → Fehler + Wiederholen"; Agent-Command meldet „Queued · id …". | `pages/Agents.tsx:446`, `pages/Settings.tsx`. | **P2**. |
| **Paginierung:** Alerts-Seite zeigt 50 der 200 gelieferten Zeilen (`slice(-50)`), keine Seiten; Portfolio nutzt Auf-/Zuklappen. | Code. | **P2**. |
| Kein `jq`, kein Recharts (entfernt #240), keine neuen Deps nötig. Vendor: react 133 kB, icons 36 kB. | Build. | ✓ |

Alle 365 Frontend-Tests grün (`vitest`, 28,6 s). Build 22,95 s.

### Erhaltene Fortschritte (nicht anfassen, nur einordnen)
Fokus-Modus (alles/problem), Executive Snapshot, Akute Punkte inkl. Live-Prä-Regs, Wahrheitsstatus-Leiste, Edge-Wahrheit, Status-SSOT (`lib/status.ts` + `StatusPill`), Viz-Primitives, Explainer-Overlays, bedarfsgeladene Unterseiten (`lazy()` in AppShell), geteilter Portfolio-Snapshot, ehrliche Fehler-/Leerzustände (#920/#925/#933), PreparedPanel ohne Fake-Prozent, Roadmap-Frische.

---

## 1. Abgleich mit der DALI-Datei (Master-Spec v2, 2026-05-13)

| v2-Punkt | Stand heute | v2.1 |
|---|---|---|
| G1 Verständlichkeit | Umgesetzt (LABEL_DE, deutsche Spalten). Restposten: Agent-Status „live/prepared/offline", aria-labels englisch, „n/v", „Stub (P1)". | T4, T0 |
| G2 Erkennung auf einen Blick | Verdicts vorhanden. Fehlt: Kernaussage + nächste Aktion je Übersichtskarte. | T5 |
| G3 Unfertige Features markiert | `DevelopmentStatus` bewusst entfernt (2026-06-03: kein Fake-Prozent). PreparedPanel mit Status-Badge. Fehlt: „Termin offen", Bündelung unter „Geplant". | T5 |
| G4 Regenbogenlinien integriert | Alle Seiten `divider={false}`; Lichtkante auf erster Card. Aber: Portfolio trägt sie auf **4** Cards, PreparedPanel auf jeder Karte, alle endlos animiert. | T3 |
| G5 Status-Spiegel | `StatusBadge` (live/paper/prepared/planned) existiert, wird kaum genutzt; Status-SSOT `StatusPill` ist die spätere, breitere Lösung. | T1 (Lage-Streifen nutzt StatusPill) |
| M1a Quality-Bar | Verdict ✓. Bedeutung/Handlungsbedarf ✗. | T5 |
| M1b Signal-Matrix | Grid-Alignment ✓ (tabular-nums, feste Spalten). Mobil ✗ (520 px Mindestbreite, verschachtelter Scroll). | T5 |
| M1c Directional Alerts | Spalten ✓. P-Bänder ✗. | T5 |
| M1d Vorbereitet-Strip | Dynamisch, ohne Fake ✓. „Termin offen"/„Geplant" ✗. | T5 |
| M2 Portfolio | Buckets/Realized/Equity als PreparedPanels ✓; Lichtkante 4× → 1×. „Guthaben, realisierte Ergebnisse, Kapitalverlauf, Risiko klar erklären": Erklärtexte in Tooltips vorhanden; Kernaussage-Zeile fehlt. | T3, T5 |
| M3 Alerts | Klartext-Spalten ✓, Versand-Diagnose ✓. Paginierung ✗. | T6 |
| M4 Risiko | Hero-Verdict ✓, Verpasste Chancen ✓. Keine Änderung. | — |
| M5 Agenten | Icons 6/11 als PNG, 5 SVG; wiring nicht sichtbar. | T4 |
| M6 Märkte / M7 News / M8 Backtesting | **Seiten am 2026-06-25 entfernt (#457).** Zuordnung heute: Märkte → „Markt-Snapshot" (TradingView) + „Markt-Regime" auf der Übersicht; News & Sentiment → Seite „Quellen" (`sources`) + Roadmap-Karte „Sentiment Stream"; Backtesting → Replay-SSOT-KPI + Nachweise (Audit-Chain/Integrität) auf der Übersicht, Verdikte in „Akute Punkte"/„Roadmaps". **Keine neuen Menüpunkte.** | T1 (Vertiefung) |
| Q1 DevelopmentStatus/StatusBadge | StatusBadge lebt; DevelopmentStatus bewusst tot (Legacy-Props in PreparedPanel entfernen). | T5 |

---

## 2. Zielbild der Startansicht (v2.1)

Vier Fragen, vier Zonen, von oben nach unten. Jede Zone hat genau **einen** Hauptplatz je Information.

```
┌ Topbar (h-14, sticky) ── Sektion / Seite · Suche · [Lage-Pills kompakt] · Dichte · Sprache · Theme · Glocke · Identität ┐
│ Lage-Streifen (sticky unter Topbar, 40 px):                                                                              │
│   ● Backend verbunden v0.x   ◔ Daten vor 2 min (Report 20:03:11Z)   ⛨ Handelsmodus: Paper · Execution aus · Write-Back gesperrt   [Fokus: Alles | Problem] │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 1 HANDLUNGSBEDARF                                                                                                        │
│   Akute Punkte (Gates, Probleme, fällige Verdikte, empfohlene Aktion)   · Premium-Runtime-Banner nur wenn blockiert     │
│   Quality-Endpoint-Fehler nur wenn Fehler                                                                                │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 2 BETRIEBSLAGE                                                                                                           │
│   „Was macht KAI gerade" = letzter Trading-Zyklus (Status, vor X min, Ergebnis) + nächster Timer   · Wahrheitsstatus-Chips │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 3 ENTSCHEIDUNGSGRUNDLAGE                                                                                                 │
│   Executive Snapshot (Kapital, Klumpenrisiko, Allocation, offene Positionen)                                             │
│   Edge-Wahrheit (Verdikt)  ·  KPI-Reihe (4 Kern-KPIs)  ·  Signal-Matrix  ·  Letzte Directional Alerts                    │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 4 VERTIEFUNG (eingeklappte Gruppen, Zustand persistiert)                                                                 │
│   ▸ Quellenqualität (Precision, Reliability, Stability, n-Übersicht)   ▸ Analysen (Quality-Bar, Signal-Qualität, Regime, Markt-Snapshot) │
│   ▸ Nachweise (Audit-Integrität, Audit-Chain, Replay, Truth-Layer, Node)   ▸ Agenten   ▸ Historie (Re-Entry, Timer)   ▸ Geplant │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Footer: KAI Control Center · Build <hash> · Paper-First  (EINZIGE Versionsangabe)                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Mobile (≤ md):** dieselbe Reihenfolge einspaltig; Lage-Streifen als zwei Zeilen; Vertiefung standardmäßig zu; Bottom-Bar `Übersicht · Signale · Portfolio · Alerts · Mehr` (öffnet den bestehenden Drawer), `padding-bottom: env(safe-area-inset-bottom)`, Items ≥ 44 px, `aria-current="page"`.

**Kartenschema (jede Übersichtskarte):** Titel · Kernaussage (ein Satz, aus Daten) · Status-Pill (Text + Symbol) · optional „→ Nächste Aktion" · „Details" aufklappbar (technische Bezeichnungen, Rohwerte, Endpoint).

**Lage-Streifen — drei getrennte Wahrheiten:**
- *Backend:* `useBackendHealth` (connected/offline/unauthorized) — Farbe + Wort.
- *Datenalter:* `quality.generated_at` relativ (`formatRelative`), Schwelle stale > 2 × Poll-Intervall → Ton warn, Wort „veraltet".
- *Handelsmodus:* aus `/operator/status`: `Paper` (immer, solange Live-Gates nicht erfüllt) + `Execution an/aus` + `Write-Back frei/gesperrt`. Der Frontend-Schalter `AppState.mode` wird zur reinen **Ansichts-Einstellung** („Ansicht: Paper") umbenannt oder entfernt — Entscheidung des Operators (Default-Vorschlag: entfernen, da ohne Wirkung; Setting `confirmLive` bleibt für den Tag X).

**Bewegung:** nur drei erlaubte Bewegungen — Live-Dot (nur bei echtem `live`-Zustand), einmaliger `kai-fade` beim Einblenden, `attention-breathe-neg` nur auf **kritischen** Karten (Fehler). Lichtkante `synthwave-pulse-edge` wird statisch (ein Verlauf, kein Schweif) oder läuft 2 Zyklen und bleibt stehen. Glow im Dark-Mode um ein Drittel gedämpft.

---

## 3. Priorisierter Änderungsplan (Tranchen, je eine PR)

| # | Tranche | Inhalt | Dateien | Aufwand | Abnahmecheck |
|---|---|---|---|---|---|
| **T0** | Fundament (P0) | Sticky-Fix (`overflow-x-hidden` von `<main>` auf `body`/Wrapper mit `overflow-x: clip`), Topbar-Cluster umbrechen (Währung/Dichte/Sprache/Theme ab `<lg` in ein „Mehr"-Menü), Datenbasis-Umschalter umbrechen, Touchflächen `min-h-11 min-w-11` mobil, `focus-visible:ring-2 ring-accent`, deutsche aria-labels, `env(safe-area-inset-*)`. | `layout/AppShell.tsx`, `layout/Topbar.tsx`, `layout/Sidebar.tsx`, `panels/EdgeTruthPanel.tsx`, `index.css` | S | Harness 320/390/768/1024/1440: `docScrollW == vw`; kein Element mit `right > vw`; `sticky` bleibt bei `scrollY=800` sichtbar; 0 Targets < 44 px bei ≤ 768 px; Tab-Reihenfolge sichtbar. |
| **T1** | Startansicht-Hierarchie (P0) | Reihenfolge nach §2; Lage-Streifen (neu `components/layout/SituationStrip.tsx`, ersetzt CommandHeader-Mischung + BackendStatusBanner + Seitenkopf-Report-Zeile); Fokus-Toggle in den Streifen, persistiert (`AppState`); Vertiefungs-Gruppen als `<details>`-ähnliche Sektionen mit persistiertem Zustand; `[&>*]:h-full`/`flex-1` entfernen; Report-Zeit nur im Streifen, Build nur im Footer; „Was macht KAI gerade" aus `fetchRecentCycles(1)` + `/health/timers` abgeleitet, Stub-Pill entfällt. | `pages/Dashboard.tsx`, `components/layout/CommandHeader.tsx` → `SituationStrip.tsx`, `layout/BackendStatusBanner.tsx` (entfällt), `state/AppState.tsx`, `lib/situation.ts` (+Test) | M | 5-Sekunden-Test: Modus, größtes Problem, nächste Aktion ohne Scrollen sichtbar (1440 und 390); `grep -c generated_at` in Dashboard-Render = 1; Version genau 1× im DOM; Tests für `situation.ts` (Datenalter-Schwellen, Modus-Ableitung). |
| **T2** | Mobile-Navigation (P0) | `layout/BottomNav.tsx` (`md:hidden`, 4 Ziele + Mehr), Drawer bleibt für Rest; Foldable: Breakpoint-Wechsel erhält Route (Hash) und Drawer-Zustand; `Übersicht → Details → zurück` mit erhaltenem Fokus-/Gruppen-Zustand (AppState). | `layout/AppShell.tsx`, `layout/BottomNav.tsx`, `layout/Sidebar.tsx`, `i18n/strings.ts` | S–M | 320/390: Bottom-Bar sichtbar, 5 Items ≥ 44×44, kein Überlappen mit Inhalt (`pb`), Bildschirmtastatur (Settings-Input fokussieren) verdeckt keine Aktion; Drehen/Breitenwechsel 390↔768 behält Route + Eingaben. |
| **T3** | Bewegung & Glow (P1) | `attention-breathe-warn` entfernen (nur neg), `synthwave-pulse-edge` statisch oder 2 Zyklen (`animation-iteration-count: 2; animation-fill-mode: forwards`), Dark-Glow −33 %, `animate-pulse` nur bei `kind=live`; Portfolio: Lichtkante nur auf erster Card; PreparedPanel ohne Lichtkante. | `index.css`, `kpi/KpiCard.tsx`, `pages/Portfolio.tsx`, `panels/PreparedPanel.tsx`, `ui/LiveDot.tsx` | XS–S | Browser: laufende Animationen nach 20 s ≤ 3 (Live-Dot, kritische Karte); `prefers-reduced-motion` → 0. |
| **T4** | Agenten (P1) | `wiring` in `AgentSummary`; Badge „autonom (Worker)" / „interaktiv (Claude Code)"; Untertitel korrigiert („3 autonom, 8 interaktiv"); Status deutsch mit Symbol (aktiv ● / bereit ○ / nicht verfügbar ✕); `AgentIcon` einheitliche Kachel 36 px mit Rahmen für PNG **und** SVG; Asset-Pipeline: Einstein/KAI-Finder/Xqu Originale → 384×384 PNG < 150 kB, alle 9 PNG neu komprimieren; Red-Team/DQI bleiben SVG (kein Original → nichts erfinden). | `lib/api.ts`, `pages/Agents.tsx`, `panels/AgentsStatusCard.tsx`, `agents/AgentIcon.tsx` (+Test-Parität), `public/agents/*.png` | M | `AgentIcon.test`: 11 Slugs; Roster-Karte zeigt 3× „autonom"; Summe `public/agents` < 1,0 MB; jede Kachel gleiche Box. |
| **T5** | Karten-Inhalte (P1) | Quality-Bar: Kernaussage + „→ Aktion" je verfehltem Ziel; Directional Alerts: Priorität als „9 · kritisch" mit Tooltip Skala 1–10, Gate ≥ 7, High-Conviction 10; Dokument-Spalte = Quelle, Hash im title; Signal-Matrix mobile Listenansicht (`md:hidden`) + Desktop ohne `min-w`; „Geplant"-Sektion mit Status/nächster Schritt/„Termin offen", AI-Insights-Karte → Route `ai`; Portfolio-Karten Kernaussage-Zeile (Guthaben, realisiert, Kapitalverlauf, Risiko). | `panels/QualityBar.tsx`, `panels/RecentAlertsCard.tsx`, `panels/SignalHeatmap.tsx`, `pages/Dashboard.tsx`, `pages/Portfolio.tsx`, `lib/labels.ts` | M | Snapshot-Tests der Karten; 390 px: Signal-Matrix ohne horizontalen Scroll; jede Übersichtskarte hat Titel+Kernaussage+Status im DOM. |
| **T6** | Laden & Daten (P2) | Lightning- und Diversification-Provider analog `PortfolioSnapshotProvider`; TradingView erst auf Klick „Chart laden" (Karte zeigt vorher Symbol/Regime); Alerts-Seite Paginierung (25/Seite, Position in Hash `#alerts?p=2`); Speicher-Feedback-Muster `useSaveState` (Wird gespeichert / Gespeichert / Fehler + Wiederholen) in Settings + Agent-Command. | `state/*Provider.tsx`, `panels/LightningPanel.tsx`, `panels/NodeStatusKpi.tsx`, `trading/tradingview/*`, `pages/Alerts.tsx`, `lib/useSaveState.ts` (+Test), `pages/Settings.tsx`, `pages/Agents.tsx` | M | Netzwerk-Tab: je Endpoint 1 Abruf je Intervall; kein TradingView-Request vor Klick; Alerts: 200 Zeilen → 8 Seiten, Zurück behält Seite; Save-Zustände unterscheidbar. |
| **T7** | Abnahme & Messung | Messprotokoll §5 vor/nach unter gleichen Bedingungen auf dem Pi (Preview-Bundle), Harness-Skript in `web/scripts/` (kein neues Framework). | `web/scripts/breakpoint-harness.mjs` | S | Tabelle §5 vollständig, keine unbelegten Claims. |

Reihenfolge: T0 → T1 → T2 → T3 → T4/T5 parallel → T6 → T7. Alles Frontend, **kein Backend-Endpoint nötig** (alle Felder existieren: `wiring`, `execution_enabled`, `write_back_allowed`, `generated_at`, `recent-cycles`, `/health/timers`). Bündelbar: T0+T3 (XS/S) in einer PR, T4+T5 in einer.

---

## 3a. DALI-Diff-Proposals (Modus `implement`, 2026-09-15)

DALI hat 24 Findings (P0 3 · P1 9 · P2 12) und 12 Patch-Proposals als unified diffs erzeugt. **Die Dropbox-Dateien `artifacts/agents/dali/{findings,proposals,runs}.jsonl` wurden NICHT geschrieben** — der Schreibzugriff des Agenten wurde vom Auto-Modus blockiert; ich habe die Dateien nicht stellvertretend angelegt. Die Diffs liegen als `P1.diff … P12.diff` im Session-Scratchpad (`…\scratchpad\dali\patches\`), der Generator `emit.py` daneben; Übernahme in die Dropbox ist Operator-Entscheidung.

| ID | Datei(en) | Inhalt | Risiko | Mein Review |
|---|---|---|---|---|
| DALI-P-001 | `layout/AppShell.tsx` | `overflow-x-clip` statt `-hidden` (Sticky-Fix), grüne Live-Linie raus, `MobileBottomNav` (4 Ziele + Mehr, 56 px, safe-area, `aria-current`) | mittel | ✓ entspricht T0+T2. `overflow-x: clip` braucht Safari ≥ 16. |
| DALI-P-002 | `pages/Dashboard.tsx` | Reihenfolge: Lage-Leiste → Kopf mit persistiertem Fokus (44 px) → Akute Punkte → Fehler/Premium → Executive Snapshot → Wahrheit → Edge → KPI → Signal-Matrix → Alerts → 6 `DeepSection` (zu) → Geplant → Historie; alle `h-full`/`flex-1` raus; Report-Zeit aus dem Kopf | mittel | ✓ entspricht T1. Offen: `DeepSection`-Zustand nicht persistiert (Übersicht → Details → zurück). |
| DALI-P-003 | `components/layout/CommandHeader.tsx` | drei getrennte Pills: „Backend verbunden" / „Daten vor X · veraltet ab 5 min" / „Handel: Ausführung AN/AUS" aus `useSharedPortfolioSnapshot` | mittel | ✓ entspricht T1. Kopplung: nur innerhalb `PortfolioSnapshotProvider` mountbar. |
| DALI-P-004 | `index.css` | Dark-Halo 22 px/0.45 → 14 px/0.26; `dot-glow` ohne weißen Zweitschatten; `synthwave-pulse-edge`/`-divider` 4 Durchläufe statt endlos; `attention-breathe-*` 2 Zyklen | niedrig | ✓ entspricht T3. |
| DALI-P-005 | `kpi/KpiCard.tsx` | `attention-breathe-warn` entfernt, nur `neg` atmet | niedrig | ✓ |
| DALI-P-006 | `lib/api.ts`, `pages/Agents.tsx`, `panels/AgentsStatusCard.tsx` | `wiring` im Typ (Pflichtfeld), Badges „autonom/interaktiv", Untertitel „3 autonom, 8 interaktiv", Status „aktiv ● / bereit ○ / nicht verfügbar ✕", kein Puls, kein Report-Zeitstempel | niedrig | ✓ entspricht T4. |
| DALI-P-007 | `agents/AgentIcon.tsx` | EINE Kachel pro Agent (PNG **oder** SVG), gleicher Rahmen/Größe; Glow ein Schatten | niedrig | ✓. Befund DALI-F-018: bisher rendert das Modul für 6 Agenten PNG **und** SVG nebeneinander. |
| DALI-P-008 | `panels/RecentAlertsCard.tsx` | Priorität als „9 · kritisch" mit Skala-Tooltip; Spalte „Quelle" per `docLabel()`-Heuristik | niedrig | **⚠ Einwand:** `recent_alerts` liefert `doc_id` als 12-Zeichen-Hash-Präfix (`dashboard.py:1163`), `source_name` ist NICHT im Vertrag. Die Spalte „Quelle" zeigt damit 8 Hash-Zeichen unter falscher Überschrift. Vor Übernahme: `source_name` in `recent_alerts` aufnehmen (eine Zeile Backend, `AlertAuditRecord.source_name` existiert) — oder Überschrift „Dokument" behalten. Prioritäts-Teil ist korrekt (Bänder = `formatters.py`). |
| DALI-P-009 | `panels/SignalHeatmap.tsx` | mobile Listenansicht (`md:hidden`), Desktop-Grid ohne `min-w`, Spalten 26–34 px | niedrig | ✓ entspricht T5. |
| DALI-P-010 | `panels/QualityBar.tsx` | Kernaussage-Satz + je verfehltem Ziel Bedeutung + „Nächste Aktion" (Pflichtfelder im `Row`-Typ); Zeitstempel raus | niedrig | ✓ Struktur. **Fachtexte gegenlesen** (z. B. „schwächste Quelle drosseln" ist eine Handlungsempfehlung, die der Operator freigeben muss). |
| DALI-P-011 | `pages/Dashboard.tsx` (nach P-002) | „Vorbereitet" → „Geplant · Termin offen", je Karte nächster Schritt, AI-Insights-Karte verlinkt Route `ai` | niedrig | ✓ entspricht T5. |
| DALI-P-012 | `layout/Sidebar.tsx`, `layout/Topbar.tsx`, `trading/ModeSelector.tsx` | Nav-Items/Buttons mobil 44 px, `focus-visible`-Ring, deutsche aria-labels, `aria-current`; ModeSelector neutral als „Vorwahl: Paper" (kein Puls, nur `live` rot), Sidebar-Pille „Ansicht: Paper" | mittel | ✓ T0. DALI wählt „Vorwahl" statt Entfernen — Operator-Entscheidung §7.1 bleibt offen. |

**Prüfstand:** `git apply --check` gegen `ff074d48`: 11/12 sauber, P-011 setzt P-002 voraus (erwartet). **Stand 2026-09-15 abends (nach Freigabe):** alle 12 Diffs im Worktree angewandt; P-008 um `source_name` korrigiert (Backend liefert das Feld jetzt; Builder nach `app/api/routers/dashboard_recent_alerts.py` ausgelagert, weil `dashboard.py` God-File mit Baseline 3015 ist). Dropbox-Dateien geschrieben (`artifacts/agents/dali/`, gitignored; Diffs archiviert unter `patches_20260915/`). Zusätzlich umgesetzt: Topbar-Faltung bis xl (Überlauf), Edge-Umschalter umbrechend, Sticky-Offset `top-14`, Touchmaße bis 768 px, Persistenz der Vertiefungsgruppen, Lightning-Dedupe, TradingView auf Klick ohne Preload, Alerts-Paginierung, `useSaveState`, Breakpoint-Harness, Planungszeile 'Nächster Schritt · Termin offen' auf Portfolio/Risiko, Lichtkante auf den Agenten-Karten. Nicht als Diff geliefert (mit Grund): Doppelabrufe/TradingView-Lazy (T6, Datenfluss), Speicher-Feedback (T6, neue Primitive), PNG-Pipeline (Binärdaten), KPI-Hierarchie (welche 4 führen: Operator), Kontrast der Neon-Tokens (nicht gemessen; im Code als „Vibe vor Lesbarkeit" akzeptiert — gehört ins Risikoregister).

## 4. Bewusst NICHT geplant
- Kein Backend-Umbau, keine neuen Endpoints, keine Schema-Felder.
- Keine neuen npm-Abhängigkeiten, kein Chart-Framework, keine Deko-Bibliothek.
- Kein Rückbau der Arcade (`components/arcade`) — nicht auf der Startseite.
- Kein Wiederaufbau von Märkte/News/Backtesting-Seiten (#457).
- Keine Live-Trading-Schalter im Frontend; `confirmLive` bleibt als stumme Einstellung.
- Keine erfundenen Termine: jede „Geplant"-Karte ohne belegten Termin trägt „Termin offen".

---

## 5. Messprotokoll (vorher / nachher, gleiche Bedingungen)

Gleiche Bedingungen = Pi `192.168.178.23`, Preview-Bundle aus derselben Mainline, Chrome Desktop 1440×900 + Emulation 390×844, Cache leer, Backend erreichbar, dreimal messen, Median.

| Metrik | Vorher (ff074d48, lokal) | Nachher | Bedingung |
|---|---|---|---|
| Build-Zeit | 22,95 s | 28,05 s (kalt) | `npm run build`, Node 22.23.2 |
| `dist` gesamt | 3 149 599 B | 2 334 818 B | `du -sb dist` |
| Initial-JS (`index-*.js`) | 287 572 B (gz 79,3 kB) | 302 440 B (gz 82,9 kB): Bottom-Nav, Mehr-Menü, Vertiefungsgruppen | Vite-Report |
| CSS | 69 781 B | 70 270 B | Vite-Report |
| Agent-PNGs | 1 172 130 B (6) | 331 078 B (9) | Ziel < 1,0 MB für 9 erreicht |
| Persona-Assets (`kai_master_v1.png` 705 kB, `kai_idle_loop.webm` 385 kB) | im dist, Referenz nur `kai/assetMapper.ts`; Widget seit 2026-09-08 entfernt | — | prüfen, ob noch geladen; sonst aus dem Build nehmen |
| Seitenhöhe Übersicht @320 px (ohne Backend) | 7 039 px | 4 571 px (Vertiefung zu) | Harness |
| Bedienelemente < 44 px @320 / @390 / @768 | 39 / 39 | 0 / 0 / 0 | Harness |
| Horizontaler Überlauf @320 / @768 / @1024 | 351 / 1 047 / 1 284 px | 0 / 0 / 0 | Harness |
| Laufende Animationen | 36 (@1920, t ca. 8 s) | 20 (t ca. 40 s; ausschließlich `pulse` der Lade-/Live-Punkte, weil ohne Backend alles lädt) | Harness; mit Backend erneut messen |
| Ladezeit bis Lage-Streifen sichtbar | **nicht gemessen** (kein Backend im Preview) | — | Pi, DevTools LCP |
| Übertragene Daten je 5 min | **nicht gemessen** | — | Pi, DevTools Network |
| Speicherbedarf (JS-Heap) | **nicht gemessen** | — | Pi, DevTools Memory |
| Speicherantwortzeit (Settings speichern) | **nicht gemessen** — es gibt heute keinen Backend-Save; localStorage synchron | — | nach T6 |
| Frontend-Tests | 365 / 365 | 369 / 369 | `vitest run` |
| Sticky Lage-Leiste bei scrollY 800 | top −700 (weggescrollt) | top 56 (unter der Topbar) | Harness |

---

## 6. Abnahmecheckliste (Sprint-Prompt §7)

- [ ] 320 / 390 / 768 / 1024 / 1440, hoch/quer, 200 % Zoom: `docScrollW == vw`, kein Element `right > vw`, keine überlappenden sticky-Leisten (Topbar + Lage-Streifen: `top` gestaffelt 0 / 56 px).
- [ ] Foldable 390 ↔ 768: Route, Drawer-Zustand, Settings-Eingaben bleiben.
- [ ] Zustände unterscheidbar: laden (Skeleton-Text „lädt …"), leer (EmptyState), veraltet (LiveDot stale + Wort), Fehler (neg + Wiederholen), Erfolg (pos + Wort).
- [ ] Jede wesentliche Information genau ein Hauptplatz (Report-Zeit 1×, Version 1×, Backend 1×, Handelsmodus 1×).
- [ ] 5-Sekunden-Test: Modus, größtes Problem, nächste Aktion ohne Scrollen (1440 + 390).
- [ ] Messtabelle §5 vollständig ausgefüllt.
- [ ] Bestehende Funktionen bedienbar: Fokus-Modus, Dichte, Sprache, Theme, Währung, Command-Palette, alle 15 Routen.

---

## 6a. Abgleich mit dem Operator-Brief vom 13.05. (`Desktop\KAI Text\DALI hier noch folgende Anfragen ... .txt`)

Der Brief ist die Quelle der Master-Spec v2 (M1–M8 = Punkte 1–8). Stand nach v2.1: Punkt 1 Quality-Bar, Signal-Matrix, Alerts, Vorbereitet erledigt · Punkt 2 Portfolio: Linie, Texte, Planungszeile erledigt · Punkt 3 Alerts: Linie auf Versand-Diagnose, Klartext, Paginierung erledigt · Punkt 4 Risiko: Linie, Texte, Planungszeile erledigt · Punkt 5 Agenten: Linie auf den Agenten-Karten, Symbole (6 vorhandene + 3 neue Portraits), Klartext erledigt · Punkte 6–8 Märkte/News/Backtesting: Seiten am 25.06. entfernt (#457), Zuordnung in §1.

## 7. Offen (Entscheidung Operator)
1. `AppState.mode` (Paper/Sim/Live-Schalter): **entfernen** (Empfehlung) oder als „Ansicht" beschriften?
2. Persona-Assets (1,1 MB) aus dem Build nehmen, wenn nach dem Widget-Rückbau unreferenziert?
3. Originalgrafiken Einstein/KAI-Finder/Xqu übernehmen (Asset-Pipeline wie v2 S2)?
4. Bottom-Bar-Ziele: Vorschlag `Übersicht · Signale · Portfolio · Alerts · Mehr` — Alternative statt Signale: `Trades`.
