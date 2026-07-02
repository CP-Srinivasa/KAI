# DALI Trades-Arcade Spec

Branch: claude/dali-trades-arcade-20260512
Worktree: C:/Users/sasch/.local/bin/kai-dali-trades-arcade
Basis-HEAD: c4377d4 (claude/dali-patches-20260510 inkl. T5/T6/T7)

---

## 0. Drift-Awareness-Notiz

Branch claude/dali-patches-20260510 hat zwei nicht im Operator-Brief erwaehnte Folge-Commits:
- cdce22e DALI-T6: Triage-Strip + Tabs + Pipeline-Stepper in Signals.tsx
- c4377d4 DALI-T7: 6-Schritt-PasteStepper + Synthwave-Top-Border am Textarea + Triage-Strip in ExternalSignals.tsx

Konsequenz:
- 0 Trades von 30 Cycles sitzt NICHT in ExternalSignals.tsx, sondern in Trades.tsx Z.245-251 (Kpi label=Ausgefuehrte Trades, value=0, sub=von 30 Cycles in der juengsten Historie).
- Die freischwebende Regenbogenlinie ist in ExternalSignals.tsx weg (divider=false Z.1144). Sie schwebt NOCH in Trades.tsx (PageHeader Z.178-191 ohne divider=false).
- Pacman lebt ausschliesslich in Trades.tsx Z.67-117. Signals.tsx und ExternalSignals.tsx kennen ihn nicht.

---

## 1. Audit-Findings

### F-001 Regenbogenlinie schwebt nur noch auf Trades.tsx (P1)
- Quelle: web/src/layout/PageHeader.tsx Z.73 Default divider=true.
- Trades.tsx Z.179-189 ruft PageHeader ohne divider=false auf. Erste Card (Cycles-Liste) hat zusaetzlich synthwave-pulse-edge. Doppelte Linie.
- Fix: Trades.tsx setzt divider=false. synthwave-pulse-edge wandert vom Cycles-Panel an die ERSTE Card (Cycles-Healthcheck).

### F-002 0 Trades von 30 Cycles als Hero-KPI demoralisiert und erklaert nichts (P0)
- Quelle: Trades.tsx Z.245-251.
- Operator-Brief Punkt 13: 30 analysiert / 12 valide / 5 ausgefuehrt / 4 risk-blocked / 3 API-Fail / Systemstatus.
- Datenlage: RecentCyclesSummary.status_counts liefert genau diese Buckets. KEIN Backend-Change noetig.
- Fix: Hero-KPI ersetzen durch Cycles-Healthcheck-Card mit 5 semantischen Buckets + Systemstatus-Headline.

### F-003 Pacman ist statisches Hero-Banner, keine Dauerschleife (P1)
- Quelle: Trades.tsx Z.195-235.
- Heute: Pacman steht links, Pellet-Reihe rechts daneben. Pacman bewegt sich nicht (nur Mund klappt).
- Operator-Brief 1-4: Pacman soll Bahn ziehen, Coins/Geister fressen, Score sichtbar, Anzahl auf einen Blick.
- Fix: Arcade-Cycle-Buehne als eigener Block:
  - Pacman links, CSS-Animation translateX 0 bis 100%, 18s linear infinite, prefers-reduced-motion pausiert.
  - Pellet-Strip = 30 Cycle-Pellets statisch positioniert. Pacman faehrt drueber. RAF/IntersectionObserver triggert Scale-Pop bei Passage.
  - Score-Counter oben rechts: SCORE 5xCoin / GHOSTS 4 / STREAK 0.
  - Hover Pellet zeigt Tooltip Symbol+Status+Dauer.
  - Mobile unter 640px: Pacman statisch, Strip horizontal scrollbar.

### F-004 Pellet- und Geist-Kategorien-Mapping zu grob (P1)
- Heute pelletForStatus 3 Klassen: completed -> Currency, order_failed/consensus_rejected/priority_rejected -> roter Geist, sonst -> Punkt.
- Operator-Brief 2: VIER Geister-Farben:
  - Order-Fehler -> ROT (--neg)
  - Konsens-Fehler -> VIOLETT (--ai)
  - Priority/Risk -> ORANGE (--warn)
  - API/Exchange -> BLAU (--info)
- Fix: pelletForStatus auf Color-by-Category umstellen. Mapping in 4.3.

### F-005 Cycles-Tabelle wirkt wie Debug-Overlay (P0)
- Quelle: Trades.tsx Z.372-409 CycleRow. 5-Spalten-Table mit Pipeline-Dots + Snake-Case-Notes-Pillen.
- Operator-Brief 5+9: Cycle-Karten mit Status, kurzer Erklaerung, Pair, Ergebnis, Risiko, Dauer, Zeit, Farbe, Symbol.
- Fix: Desktop wechselt auf Cycle-Card-Liste:
  - 4px Tone-Bar links (CompletedPos / RiskAi / DataWarn / FailNeg / Empty)
  - Header: Cycle-Nr + Symbol (mono, bold) + Status-Badge + Dauer-Pille + relative Zeit
  - Body: CYCLE_STATUS_REASON 1-Zeile
  - Footer: 5-Dot-Mikro-Pipeline + Notes-Pillen
- PnL/Risk/Confidence kommen NICHT aus TradingCycle (lib/api.ts Z.452-467). Pseudo-Felder NICHT erfinden. Operator-Frage 1.

### F-006 Schutzschalter-Slugs lesen technisch (P1)
- Quelle: Trades.tsx Z.307-345.
- Heute: Real-Order-Execution / Trading-Journal Schreiben / Run-Once Trigger.
- Operator-Brief 16: Operator-Sprache, klare Bedeutung pro Schalter in 1 Satz.
- Fix: Microcopy-Tabelle in 3.

### F-007 Run-Once nur PreparedPanel-Stub (P0)
- Quelle: Trades.tsx Z.410-414.
- Operator-Brief 19+20: Run-Once braucht echte UI als Arcade-Launch-Button + Bestaetigungs-Flow + clientseitige Idempotency-Key-Generierung.
- Backend POST /operator/trading-loop/run-once: Operator-Frage 2.
- Fix: PreparedPanel ersetzen durch RunOnceLauncher-Component (Symbol-Picker + Idempotency-Key + Bestaetigungs-Modal + Effekt-Liste).

### F-008 TradingLoopCard 4-Klassen vs. Operator-Geister 4-Farben inkonsistent (P2)
- Quelle: web/src/components/panels/TradingLoopCard.tsx Z.18-43.
- Heute healthy/risk/data/fail. Operator-Brief 2: Order/Konsens/Priority-Risk/API-Exchange.
- Heute consensus_rejected gehoert zu risk. Operator will violett=Konsens vs orange=Priority/Risk.
- Fix: Optional T6-Refactor (single source of truth). Out-of-Scope. Trades-Page nutzt das neue Schema (4.1).

### F-009 A11y bei rein-visueller Pacman-Story (P2)
- Heute aria-hidden=true auf SVG-Pacman + Pellets. Screen-Reader hat keinen Status.
- Fix: Arcade-Buehne role=img + aria-label mit Klartext-Summary. Caption darunter sichtbar.

### F-010 i18n.pages.trades.sub ist falsch (P2)
- Quelle: i18n/strings.ts Z.255 (Offene und geschlossene Positionen / Realized / Unrealized / Fees). Die Seite zeigt KEINE Positionen.
- Fix: Sub umschreiben zu (Was hat KAI entschieden und mit welchem Ergebnis?).

---

## 2. Wireframes (ASCII)

### 2.1 Trades-Page neue Vertikale

PageHeader: TRADES (tone=pos), [Aktualisieren] rechts, divider=false
Sub: Was hat KAI entschieden und mit welchem Ergebnis?

[Cycles-Healthcheck Card mit synthwave-pulse-edge an Top-Border]
Headline: 30 Cycles analysiert / Systemstatus: STABIL
5 Buckets: 30 analysiert | 12 valide | 5 ausgefuehrt | 4 risk-blocked | 3 API-Fail
Klartext-Zeile: Letzte 30 Cycles: 5x ausgefuehrt, 12x kein Signal, 4x Konsens...

[Arcade-Cycle-Buehne]
Score oben rechts: SCORE 5xCoin / GHOSTS 4 / STREAK 0 (zuletzt order_failed)
Bahn: Pacman wandert links-rechts, Pellet-Strip statisch dahinter
Legende: Coins=Trade / Geist rot=Order / violett=Konsens / orange=Risk / blau=API

[Schutzschalter-Trio]
ECHTGELD-TRADING: laeuft/gesperrt + 1-Satz-Hint
TRADING-JOURNAL: schreibt mit/read-only + 1-Satz-Hint
MANUELLER LAUNCH: bereit/blockiert + 1-Satz-Hint

[Run-Once Arcade-Launcher]
Symbol-Picker (BTC/ETH/SOL/XRP/USDT)
Mode-Lock-Anzeige (zeigt aktuellen Mode paper/sim/live)
Idempotency-Key clientseitig generiert + regenerier-Button
Grosser Arcade-Button CYCLE LAUNCHEN mit warn-Glow
Effekt-Liste darunter: 1 Datenfetch -> 1 Signal -> 1 Risk -> 1 Order. Auto-Loop unberuehrt.

[Letzte Trading-Cycles als Card-Liste, kein Table mehr]

### 2.2 Cycle-Card-Detail

4px Tone-Bar links (Outcome-Klasse)
Header: Cycle-Nr | Symbol (mono, bold) | Status-Badge | Dauer-Pille | relative Zeit rechts
Body: 1-Zeilen-Erklaerung aus CYCLE_STATUS_REASON
Footer: 5-Dots-Mikro-Pipeline (Daten-Signal-Risk-Order-Fill) + Notes-Pillen

### 2.3 Pacman-Buehne Live-Behavior

- Container min-h 96px, overflow-hidden, position-relative
- Pacman absolute, CSS animation pacman-march translateX(0) bis calc(100% - 32px), 18s linear infinite
- Pellet-Strip statisch positioniert (Operator sieht zu jeder Zeit alle 30)
- prefers-reduced-motion: animation-play-state paused, Strip bleibt
- Mobile unter 640px: Pacman statisch links, Strip overflow-x-auto

---

## 3. Microcopy-Tabelle (Alt -> Neu)

| Ort | Alt | Neu |
|---|---|---|
| i18n strings.ts:255 pages.trades.sub | Offene und geschlossene Positionen / Realized / Unrealized / Fees | Was hat KAI entschieden und mit welchem Ergebnis? |
| Trades.tsx:184 PageHeader.sub override | Mode: paper / Letzter Status: completed | Mode: paper / Letzter Cycle: vor 14m / Trade ausgefuehrt |
| Trades.tsx:248 Kpi-Hero | 0 / von 30 Cycles in der juengsten Historie | (gestrichen - ersetzt durch Cycles-Healthcheck) |
| Trades.tsx:312 GuardrailPill #1 Label | Real-Order-Execution | Echtgeld-Trading |
| Trades.tsx:312 Pill #1 ON-Hint | Echte Orders werden auf der Boerse platziert. | Echte Orders gehen JETZT an die Boerse. Stoppt via Modus paper. |
| Trades.tsx:312 Pill #1 OFF-Hint | Nur Paper/Shadow keine echten Orders. | Nur Simulation - keine echte Ausfuehrung. |
| Trades.tsx:321 GuardrailPill #2 Label | Trading-Journal Schreiben | Trading-Journal |
| Trades.tsx:321 Pill #2 ON-Hint | Cycles werden ins Journal geschrieben. | Jeder Cycle wandert ins Audit-Log - vollstaendige Forensik-Spur. |
| Trades.tsx:321 Pill #2 OFF-Hint | Read-only Journal ist gesperrt. | Read-only - keine Cycles werden persistiert. |
| Trades.tsx:331 GuardrailPill #3 Label | Run-Once Trigger | Manueller Cycle-Launch |
| Trades.tsx:331 Pill #3 ON-Hint | Operator kann manuell einen Cycle anstossen. | Du kannst jetzt einen einzelnen Cycle anstossen (im aktuellen Modus). |
| Trades.tsx:331 Pill #3 OFF-Hint | Run-Once aktuell nicht moeglich. | Aktuell gesperrt - siehe Grund. |
| Trades.tsx:411 PreparedPanel-Title | Guarded run-once trigger | Run-Once - Arcade Launch |
| (neu) Healthcheck-Headline | --- | 30 Cycles analysiert / Systemstatus: stabil/Aufmerksamkeit/instabil |
| (neu) Bucket-Labels | --- | analysiert / valide Signale / ausgefuehrt / risk-blocked / API-Fail |
| (neu) Arcade-Score | --- | SCORE: 5xCoin / GHOSTS: 4 / STREAK: 0 |
| (neu) Arcade-Legende | --- | Coin = Trade ausgefuehrt / Geist rot=Order / violett=Konsens / orange=Risk / blau=API / Punkt=kein Anlass |
| (neu) Run-Once Launch-Button | --- | CYCLE LAUNCHEN uppercase Arcade-Glow |
| (neu) Run-Once Was-passiert | --- | 1 Datenfetch -> 1 Signal-Analyse -> 1 Risk-Check -> 1 Order. Auto-Loop bleibt unberuehrt. |
| (neu) Cycle-Card Dauer | --- | 320ms / 1.4s / laeuft |
| (neu) Cycle-Card Relativ-Zeit | --- | vor 14m / vor 2h |


---

## 4. Datenfluss und Color-Mapping

### 4.1 Field-Mapping

| UI-Block | Datenquelle (lib/api.ts) | Fallback |
|---|---|---|
| Cycles-Healthcheck analysiert | recent_cycles.length | 0 / Empty-State |
| valide Signale | Count(status in completed, risk_rejected, consensus_rejected, order_failed) | 0 |
| ausgefuehrt | status_counts.completed | 0 |
| risk-blocked | status_counts.risk_rejected + consensus_rejected + priority_rejected | 0 |
| API-Fail | status_counts.no_market_data + stale_data + order_failed | 0 |
| Systemstatus-Headline | abgeleitet 4.2 | Daten werden geladen |
| Arcade-Score Coins | Count(completed) | 0 |
| Arcade-Score Ghosts | Count(4 Fehler-Kategorien) | 0 |
| Arcade-Score Streak | Aufeinanderfolgende completed ab letztem Cycle rueckwaerts | 0 |
| Pacman-Track-Pellets | recent_cycles[].status nach 4.3 | grauer Punkt |
| Cycle-Card Symbol | c.symbol | --- |
| Cycle-Card Erklaerung | CYCLE_STATUS_REASON oder TITLE oder status | --- |
| Cycle-Card Pipeline-Dots | 5 Booleans (market_data_fetched, signal_generated, risk_approved, order_created, fill_simulated) | alle false |
| Cycle-Card Dauer | completed_at minus started_at diff ms | laeuft wenn completed_at null |
| Cycle-Card Relativ-Zeit | started_at vs Date.now | absolutes UTC |
| Cycle-Card Notes | c.notes mit humanizeNote() | (kein Block) |
| Run-Once Symbol-Picker | Frontend-Konstante BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, USDT | BTC/USDT |
| Run-Once Idempotency-Key | crypto.randomUUID() | Button disabled |
| Schutzschalter Echtgeld | status.execution_enabled | Button disabled |
| Schutzschalter Journal | status.write_back_allowed | --- |
| Schutzschalter Run-Once-Ready | status.run_once_allowed | --- |
| Schutzschalter Block-Grund | status.run_once_block_reason | Grund unbekannt |

Pseudo-Felder NICHT erfinden: PnL pro Cycle, Confidence, Risk-Level, Strategy-Name. Operator-Frage 1.

### 4.2 Systemstatus-Headline-Regelwerk

- if total == 0: Noch keine Cycles im aktuellen Fenster
- elif orderFail >= 3: INSTABIL - Execution-Layer pruefen (tone=neg, attention-breathe-neg)
- elif ghostsTotal/total > 0.4: Aufmerksamkeit noetig (tone=warn)
- elif executed == 0 and total >= 10: Quiet - kein Trade-Anlass (tone=info)
- else: STABIL (tone=pos)

### 4.3 Pellet- und Geist-Color-Mapping (Operator-Brief 2)

| Cycle-Status | UI-Klasse | Token | Pellet-Form |
|---|---|---|---|
| completed | TRADE | --pos rotierend mit info/ai/warn | Currency-Coin (Dollar/Euro/Yen/Pound/B) |
| order_failed, sl_failed | GHOST-ORDER | --neg | Geist rot |
| consensus_rejected | GHOST-CONSENSUS | --ai | Geist violett |
| priority_rejected, risk_rejected, gate_blocked, signal_below_threshold | GHOST-RISK | --warn | Geist orange |
| no_market_data, stale_data | GHOST-API | --info | Geist cyan/blau |
| no_signal | EMPTY | --fg-subtle/25 | kleiner Punkt |
| blocked | EMPTY | --fg-subtle/25 | kleiner Punkt |
| (unbekannt) | EMPTY | --fg-subtle/15 | kleiner Punkt |

KEIN neuer Magic-Hex. Alle 5 Toene in index.css Z.21-27 (--pos, --neg, --warn, --info, --ai).

### 4.4 Wiederverwendete Tokens

- synthwave-pulse-edge (index.css Z.343-365) Top-Border am Cycles-Healthcheck-Card
- glow-pos / glow-neg / glow-warn / glow-info / glow-ai Score-Pulses + Cycle-Card-Border-Glow
- attention-breathe-neg / attention-breathe-warn wenn Systemstatus INSTABIL/Aufmerksamkeit
- currency-pellet (Trades.tsx Z.137 + CSS Z.322-330) bestehende Pellet-Form
- btc-pacman (CSS Z.308-315) bestehender Pacman-Drop-Shadow
- pacman-power-pulse (CSS Z.318-321) bestehende Pellet-Pulse-Keyframes
- NEU: pacman-march Keyframes (translateX, 18s linear infinite) + prefers-reduced-motion-Override

### 4.5 A11y-Pflichten

- Arcade-Buehne role=img mit aria-label Klartext-Zusammenfassung der 30 Cycles
- Pacman pausiert via @media prefers-reduced-motion: reduce { animation-play-state: paused }
- Schutzschalter-Pills span role=status mit Status-Wort als textuelle Spiegelung
- Run-Once-Launch-Button aria-describedby=run-once-effects mit Aufzaehlung
- Cycle-Cards: Status nicht NUR Farbe - immer Wort als Badge + Icon

---

## 5. Patch-Tranchen-Plan

| # | Titel | Betroffene Dateien | LOC-Delta | Risiko | Manueller Test | Abhaengigkeit |
|---|---|---|---|---|---|---|
| T1 | Cycles-Healthcheck-Card + Regenbogenlinie-Fix | Trades.tsx (Hero-Banner ersetzen, divider=false), i18n/strings.ts (pages.trades.sub) | +120/-40 | low | (1) keine doppelte Synthwave-Linie (2) 5 Buckets + Systemstatus-Headline (3) Empty-State bei 0 Cycles | --- |
| T2 | Schutzschalter-Microcopy | Trades.tsx (3 GuardrailPill-Aufrufe + Hints) | +5/-20 | low | Operator scannt Trio in 2 Sek | T1 |
| T3 | Cycle-Card-Liste statt Tabelle (Desktop+Mobile) | Trades.tsx (CycleRow -> CycleCard) | +200/-90 | medium | (1) 30 Cards farb-codiert (2) Mobile-Stack natuerlich (3) Notes-Pillen in Footer | T1 |
| T4 | Arcade-Cycle-Buehne (Pacman wandert + Score) | Trades.tsx (ArcadeCycleStage), index.css (pacman-march + reduced-motion) | +220/-50 | medium | (1) Pacman wandert Light+Dark (2) reduced-motion pausiert (3) Score korrekt (4) Hover-Tooltips (5) 4-Geister-Farben sichtbar | T1 (Bucket-Source) |
| T5 | Run-Once Arcade-Launch-Button + Bestaetigungs-Modal | Trades.tsx (RunOnceLauncher ersetzt PreparedPanel), lib/api.ts (optional postRunOnce), i18n/strings.ts | +180/-10 | high (Backend-Trigger) | (1) Symbol-Picker (2) Idem-Key sichtbar+regenerierbar (3) Modal Effekt-Liste (4) Disabled wenn run_once_allowed=false (5) Erfolg -> Toast + reload | T1-T4 + Backend-Verfuegbarkeit |

Stop-Regeln pro Tranche:
- TypeScript-Check gruen (npm run typecheck im web/)
- Jede Tranche atomar revertbar (git revert)
- T4 + T5 ohne TS-Tests; manueller Operator-Test ist Stop-Gate

---

## 6. Offene Fragen an Operator

1. PnL / Risk-Level / Confidence pro Cycle: TradingCycle-Typ hat KEINE dieser Felder (lib/api.ts Z.452-467). UI-Spec ohne PnL/Risk (Frontend-only) ODER Backend-Erweiterung Teil dieses Auftrags (out-of-DALI-Scope)?
2. Run-Once-Endpoint: Existiert POST /operator/trading-loop/run-once heute paper/sim-tauglich und akzeptiert {idempotency_key, symbol}? Bei Nein: T5 wird UI-Mock mit deaktiviertem Submit.
3. Pacman-Animation-Dauer: 18s pro Strecke OK, oder chiller 30s / nervoeser 8s? CSS-Variable laesst Anpassung zu.
4. Cycle-Card-Tabelle: Beibehalten als Density-Toggle (Tabelle vs Cards)? Vorschlag: ersatzlos raus. Toggle: +30 LOC.
5. Externe-Signale-Page: Tranche dafuer? T7 ist 2 Tage alt, sitzt sauber. Vorschlag: nicht anfassen. Falls Audit-Pass gewuenscht: separate Mini-Spec.

---

## 7. Out-of-Scope (bewusst)

- Backend-Endpoints, Schema-Erweiterungen, neue API-Felder (PnL/Confidence/Risk-Level) sind Architect/Codex-Scope
- Mobile-spezifische Pacman-Variante ueber statisch + Strip-Scroll hinaus
- Neue npm-Dependencies
- ModeSelector.tsx (Topbar) bleibt unveraendert
- TradingLoopCard.tsx-Refactor auf 5-Klassen (F-008): eigene Tranche T6 falls Operator wuenscht
