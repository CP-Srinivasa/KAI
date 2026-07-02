# EOW-Review-Memo 2026-05-23 (Window 2026-05-16 → 2026-05-23)

**Status:** Validierungs-/Snapshot-Sitzung (kein Decision-Tag).
**Operator-Sign-off vom 21.05.:** Variante 1 = Priority-Scoring **Option D** (Status quo bis 2026-05-30) + Phase-2 **Option B** (Shadow +7d). Decision-Pflicht **bleibt verschoben auf 2026-05-30**.
**Querverweise:** `priority_scoring_decision_brief_2026-05-23.md`, `re_entry_end_of_window_2026-05-23.md`, `priority_scoring_inspection_2026-05-20.md`, `bayes_decision_basis_2026-05-21.md`, `/mnt/kai-data/eow_snapshots/priority_scoring_eow_snapshot_2026-05-23.md`.

---

## 1. Snapshot-Daten 23.05. vs. Baseline 20.05.

| Bucket | 23.05. n | 23.05. directional% | Baseline 20.05. n | Baseline directional% | Δ Sample | Δ directional% |
|---|---|---|---|---|---|---|
| p≥10 | **8** | 25.0% | 418 | 43.5% | ÷52 | −18.5pp |
| p=8/9 | **12** | 8.3% | 475 | 87.8% | ÷40 | −79.5pp |
| p<8 | **85** | 0.0% | 917 | 31.3% | ÷11 | −31.3pp |
| **Σ Alerts mit sentiment_label** | **105** | — | **1810** | — | ÷17 | — |

**Interpretation:**
- Das 7d-Fenster hat **17× weniger Sentiment-getagte Alerts** als die 20.05.-Baseline. Damit ist die Cross-Tab heute **nicht statistisch inferenzfähig** — sie ist Indizien-Ebene.
- Die *qualitative* Richtung des NEW-1-Befundes (p≥10 unter p=8/9 in directional%, p<8 erwartungsgemäß bei Null) ist auf der Mini-Stichprobe **partiell widersprüchlich**: p≥10 zeigt 25.0% gegen p=8/9 mit nur 8.3% — d.h. p≥10 *steht hier sogar besser da*. Das ist Sample-Rauschen (n=8 vs. n=12), nicht Refutation der Baseline-Diagnose.
- **5 von 8** p≥10-Alerts im 7d-Fenster sind `mixed` (kein bullish/bearish-Vorzeichen). Das deckt sich weiter mit der NEW-1-Hypothese "Priority ignoriert Sentiment-Klarheit".
- **2 von 12** p=8/9-Alerts sind `bullish`, 0 bearish, 9 `mixed`, 1 `neutral` — Sentiment-Verteilung in dieser Klasse weiter dominiert von Mixed.

**Konsequenz für Operator-Sign-off:** Die Daten **widerlegen Option D nicht**, aber sie stützen sie auch nicht stark. Sie sind primär ein Window-Sample-Symptom.

## 2. Alert-Pipeline-Lebenszeichen (nicht im Snapshot enthalten)

- `alert_audit.jsonl` insgesamt: **7865 Einträge** (Pipeline lebt).
- Last `dispatched_at`: **2026-05-23T08:13:31 UTC** (vor ~2h 20min zur Memo-Erstellung).
- Per-Tag seit 14.05.: 31 / 18 / 3 / 11 / 15 / 11 / 22 / 24 / 17 / 3 (heute laufend).
- Seit 2026-05-21 (55h): **44 Alerts**, davon priority=7: 35, priority=9: 4, priority=10: 3, priority=8: 2 → **p≥8 nur 9/44 (20.4%)**. Sentiment: 22 mixed, 20 neutral, 2 bullish, 0 bearish.

**Befund:** Pipeline ist nicht still, sondern **strukturell low-priority**. Die `paper_min_priority=10`-Reversion (ADR-1) hat die Effekt-Quote auf ~7% (3/44) der Alerts gesetzt. Das ist ein bekanntes Designziel von Option D — Strenge gegen Channel-Bias —, aber es bedeutet operativ: organische Bayes-Erzeugung bleibt sehr selten.

## 3. Bayes-Audit-Stream (DS-20260523-V2 + V6 integriert)

- Total: **4 Einträge**, last `dec_25599792322e` 2026-05-21T04:14:07 UTC.
- **53h Stille** seit letztem Eintrag.
- Schreibrate-Trajektorie: 0.62/Tag (Stand 21.05.) → **0.47/Tag** (Stand 23.05.).
- ETA n≥20: bei aktueller Trajektorie **frühestens 2026-06-23** (−10 Tage vs. 21.05.-Hochrechnung).

**Indizienkette zur 53h-Stille (V2):**
- **H1 Channel-Quietness:** Bestätigt — `premium_signal_actions` last `2026-05-20T21:50:20` (target-completion reconcile, kein Tier-1-Signal), davor `2026-05-15`. Premium-Channel hat seit 53h kein Tier-1 geliefert.
- **H2 Priority-Gate filtert vor Bayes:** Konsistent mit Befund § 2 — nur 3 von 44 Alerts seit 21.05. erreichen p=10. SignalGenerator wird nur über p≥10-Alerts mit Premium-Signal ausgelöst.
- **H3 Adapter-Bug:** **Nicht belegt.** journalctl `kai-server` seit 21.05. zeigt **0 Treffer** für "bayes|audit_adapter|confidence". Bei einem Schreib-Bug wäre mindestens ein Error-Log zu erwarten. Stille im Log ist konsistent mit "kein Aufruf passiert", nicht mit "Aufruf aber Schreibfehler".

**Schlussfolgerung V2/V6:** Bayes-Stille ist **strukturell** durch ADR-1 (Priority-Gate=10) + Channel-Quietness (Premium-Tier still) erklärt, nicht durch einen Pipeline-Defekt. Empfehlung: SHADOW_ONLY bleibt `true` bis Heuristik (Memory-Pin `bayes_shadow_only_flip_heuristik`) erfüllt — bei aktueller Trajektorie organisch erst ~6 Wochen entfernt. Falls Operator beschleunigen will: separate Sprint-Story "Signal-Breite erhöhen" (Whale-Alert / Funding-Divergenzen) nach 30.05.-Decision.

## 4. Auto-Annotate-Pipeline-A 48h-Befund (DS-20260523-V3)

Reaktivierung **2026-05-21 13:21:03 CEST** (`kai-auto-annotate.timer enable --now`). Bisher **5 erfolgreiche Timer-Runs** (alle 6h: 13:21 → 19:21 → 01:21 → 07:21 = letzter, next 13:21 CEST). Pro Run:
- `pending_count=5`, davon `stale_backfill=5` (alle bisherigen Dokumente sind backfill-stale, kein frisches direktionales Material in 168h-Fenster).
- Volatility-Tag: `btc_24h_change=-2.71%` (heute morgen) → threshold scaled auf **2.36%**.
- Output: **5 annotated, 0 hit, 0 miss, 5 inconclusive** (jedem Run identisch).

**Befund:** Pipeline-A wirkt operativ einwandfrei, aber:
- **Threshold 2.36% bei 168h-Fenster ist zu eng**: Sample-Bewegungen +0.68% / +0.91% / -0.87% / +1.36% / +0.67% bei BTC/SOL über 7 Tage. Bei Low-Vol-Phase (BTC −2.71% 24h, ranging Regime) wird Threshold nicht durchbrochen.
- **Pipeline-A schreibt nicht in `alert_audit_outcomes.jsonl`**, sondern nur in journalctl. Die im EOW-Snapshot gemeldeten "909 inconclusive in 7d" stammen weiterhin aus Pipeline-B (CLI `auto-check` Fix-5%-Threshold-Backfill).

**Konsequenz:** Pipeline-A liefert auf die nächsten Tage **wenig zusätzliche Resolution** — solange Markt-Vol niedrig bleibt und Pipeline-A keine frischen Direktionalen findet, bleibt sie symbolisch aktiv. Empfehlung für Phase-2: (a) Window 168h → 24h evaluieren (Threshold-Forensik-Memo 22.05. hat 4 Optionen vorgemerkt), oder (b) Pipeline-A-Outcomes ins alert_outcomes-Schema mergen (separater Sprint).

## 5. Paper-Fill-Counter-Drift (DS-20260523-V4) — Re-Entry-Gate KORREKTUR

**Skeleton-Live-Metrik 23.05. zeigt:** `Paper-Trading abgeschlossene Trades: 8` → Gate `❌ 8/10 paper-fills`.

**Diese Zahl ist falsch.** Forensik:
- Generator `_paper_fills_count()` in `app/cli/commands/daily_strategy.py:81-101` zählt `event_type == "position_closed"` im **lokalen** `artifacts/paper_execution_audit.jsonl`.
- **Lokales File** (Windows-Workstation): mtime 2026-05-21 19:00 CEST, **position_closed = 8** (alt-stale, Sync-Lag).
- **Pi-File** (Source of Truth): mtime 2026-05-21 15:48 CEST (UTC 13:48), **position_closed = 15**, plus **position_partial_closed = 24**.

**Echter Stand:**
- `position_closed` Pi = **15** ≥ 10 → Re-Entry-Gate **erfüllt** (auch unter strenger Definition "nur full-close zählt").
- Inklusive `position_partial_closed`: **39 Close-Events** mit PnL.
- Re-Entry-Gate war ohnehin ein **ODER**-Gate (≥200 directional **ODER** ≥10 paper-fills) — Alert-Gate `423/200` ✅ schon erfüllt.

**Konsequenz:**
- Skeleton-Anzeige "8/10 paper-fills" ist Sync-Lag-Artefakt, kein offener Engpass.
- **Re-Entry-Gate beide Bedingungen erfüllt.** Es gibt heute keinen Re-Entry-Blocker mehr.

**Empfehlung:** Skeleton-Generator entweder (a) auf Pi laufen lassen (Timer `kai-daily-strategy.timer` läuft schon auf Pi 08:00 CEST — ist das so?), oder (b) Pi-Audit täglich vor Workstation-Skeleton-Bootstrap synchronisieren, oder (c) Counter zusätzlich `position_partial_closed` zählen (semantisch: jeder Partial-Close hat PnL). Triviale Patch-Story, P2 nach 30.05.

## 6. EOW-Snapshot-Tool Doku-Drift (DS-20260523-V5, kein Defekt)

EOW-Snapshot zeigt `SHADOW_ONLY = <unset>` weil das Tool nach literal-Key `SHADOW_ONLY` greppt. Pi-`.env` nutzt prefixed Key `RISK_BAYES_CONFIDENCE_SHADOW_ONLY=true` (Memory-Pin). **Wert ist korrekt gesetzt**, nur Display-Drift im Snapshot-Output.

Patch: Snapshot-Skript erweitern um `RISK_BAYES_CONFIDENCE_SHADOW_ONLY`-Resolution. ~15min, nicht heute eilig — kann der 30.05.-Snapshot bereits zeigen.

## 7. Sign-off-Bestätigung & Empfehlung für 30.05.

**Variante 1 (Option D) bleibt richtig.** Begründung:
- Heutige Daten widerlegen den 20.05.-Befund (Priority ignoriert Sentiment) nicht — sie sind nur sample-bedingt blass.
- Bayes-Datenbasis bleibt zu dünn für Decision-Inferenz (n=4 Einträge total, +0 organisch seit 21.05.).
- Pipeline-A reaktiviert aber strukturell unter-fed in Low-Vol-Phase.
- Re-Entry-Gate technisch erfüllt — kein Blocker.

**Empfehlung für 30.05.-Decision-Pack:**
- (a) **Robustheits-Check der Sample-Größe einbauen:** Wenn 30.05.-Snapshot wieder <200 Alerts hat, ist die Cross-Tab-Diagnose strukturell instabil. Decision-Pack muss diesen Fall explizit modellieren ("wann ist eine Sample-Größe Decision-fähig?").
- (b) **Alternativer Datenpfad:** Wenn 7d-Cross-Tab dünn bleibt, kann 28d-Cross-Tab (rolling) als Ergänzung ins Decision-Pack — mehr Sample, weniger Trend-Sensitivität.
- (c) **Variante 2 (PR #58 A'-Penalty-Patch) bleibt der natürliche Pivot,** falls 30.05.-Daten weiter NEW-1-Diagnose stützen. Bis dahin: kein Re-Open der Decision.

## 8. Offene Folge-Sprints (Backlog nach 30.05.)

- **B1 (P2):** EOW-Snapshot-Tool `RISK_BAYES_CONFIDENCE_SHADOW_ONLY`-Key-Patch + position_partial_closed-Counter.
- **B2 (P2):** Skeleton-Generator-Sync-Strategie (Pi-first oder Pre-Bootstrap-Sync).
- **B3 (P3):** Pipeline-A-Window-/Threshold-Re-Evaluation (24h vs. 168h, Outcomes-Merge ins alert_outcomes-Schema).
- **B4 (P3):** Bayes-Schreibrate-Boost via Signal-Breite (Whale-Alert / Funding-Divergenzen — Phase-2-Quellen-Backlog).
- **B5 (P3):** `kai-pi-health.timer` Aktivierung (heute als nicht-aktiv gelistet → Spec DS-20260521-C wartet auf Operator-Sign-off).

---

**Memo-Aufwand:** 45min (V2/V3/V4-Pi-Recherche ~50min davor).
**Eingriffe:** Keine. Read-only Memo.
**Decision-Pflicht heute:** Keine. Sign-off vom 21.05. bleibt gültig.
