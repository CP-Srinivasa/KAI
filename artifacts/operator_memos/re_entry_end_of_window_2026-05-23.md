# Re-Entry End-of-Window Review — 2026-05-23

**Stichtag:** 2026-05-23 (7 Tage nach Re-Entry-Eröffnung 2026-05-16 + Lernstack-Deploy R3/V1/V3/V4).
**Memo angelegt:** 2026-05-19 21:42 CEST als Skeleton (DS-20260519-7). Wird am 2026-05-23 durch Operator + Claude finalisiert.
**Status:** ⏳ pending Fill.

---

## Vorgaben für die Befüllung

Dieses Memo ist der Audit-Anchor für die End-of-Window-Decision. Es **muss** beim Fill-Termin am 2026-05-23 folgende sieben Sektionen liefern. Keine Sektion darf leer bleiben — wenn keine Daten vorliegen, ist das ein expliziter Eintrag „keine Daten — Grund: …".

---

## 1. Pi-Live-Status (Source of Truth)

_Zu befüllen am 2026-05-23:_

- `git rev-parse HEAD` + Vergleich zu `origin/claude/p7/reentry-ia-codex-cycle`.
- `/health` + `/health/premium_pipeline` (alle Checks).
- Active Services + Timer-Liste.
- `.env`-Auszug: `EXECUTION_PAPER_MIN_PRIORITY`, `RISK_BAYES_CONFIDENCE_SHADOW_ONLY`, `RE_ENTRY_MODE_ENABLED`, `LIVE_MODE`, `OPERATOR_SIGNAL_AUTO_RUN_ENABLED`.

## 2. Re-Entry-Gates (Stand 2026-05-23)

_Zu befüllen am 2026-05-23:_

| Metrik | 2026-05-17 (Baseline) | 2026-05-23 (jetzt) | Δ |
|---|---|---|---|
| Resolved directional alerts (Pi) | 382 (127 hit / 255 miss) | ? | ? |
| Paper-Trading abgeschlossene Trades | 14 | ? | ? |
| Inconclusive-Rate | 95.3% (7761/8143) | ? | ? |
| Baseline-Precision | 33.2% (Pi) | ? | ? |
| TV pending events | 15 | ? | ? |

**Sekundär-Gate Inconclusive-Rate** (siehe `re_entry_decision_2026-05-16.md` §3.5): Ziel-Threshold-Vorschlag war ≥15% hard-resolved für Active-Mode-Eligibility. Bei Fill: aktueller Wert vs. Threshold-Entscheidung diskutieren.

## 3. Lernstack-Wirksamkeit V1–V4.1

_Zu befüllen am 2026-05-23:_

| Modul | Audit-Stream | Einträge 17.05. | Einträge 23.05. | Bewertung |
|---|---|---|---|---|
| V1 Source-Reliability Wilson-Loop | `source_reliability_audit.jsonl` (oder analog) | 8 sources insufficient | ? | ? |
| V2 R3-Shadow Regime-Stamping | `regime_audit.jsonl` | ? | ? | ? |
| V3 Source-Confluence shadow audit | `source_confluence_audit.jsonl` | 40 (alle count=0) | ? | ? |
| V4 Bayes-Posterior recalc | `bayes_posterior_audit.jsonl` | 8 (letzter 15.05.) | ? | ? |
| V4.1 Position-Repair Outcome | (TBD) | ? | ? | ? |

**Posterior-Stabilität:** Hat V4 zwischen 17.05. und 23.05. neue Einträge bekommen? Wenn nein, warum nicht (Sentiment-Drift, Bridge-Filter, andere)?

## 4. Sentiment-Distribution-Drift (NEU aus 2026-05-19 V2-Forensik)

_Zu befüllen am 2026-05-23:_

- Alert-Sentiment-Verteilung 2026-05-17 bis 2026-05-23 (Vergleich zu pre-14.05.-Baseline).
- Hat DS-20260520-NEW (Pipeline-Forensik) eine Hypothese (a/b/c) bestätigt? PR #45/#46/#47-Verdacht?
- Anteil `_maybe_trigger_paper_trade()`-Triggers in 7d-Fenster.
- **Wenn Sentiment-Drift bestätigt:** Folge-Sprint-Definition (Code-Patch oder Quellen-Erweiterung) — Sign-off zwingend vor Phase-2-Start.

## 5. ADR-2..6 Re-Evaluation

_Zu befüllen am 2026-05-23:_

| ADR | Trigger erfüllt? | Decision jetzt |
|---|---|---|
| ADR-2 Tier-Verteilung | ≥3 sufficient sources? | ? |
| ADR-3 R4-Filter-Aktivierung | R3-Shadow-Statistik sauber? | ? |
| ADR-4 V3-Window | V3-Audit ≥10 confluente Cases? | ? |
| ADR-5 Bayes-Sizing | Posterior-Stabilität messbar? | ? |
| ADR-6 SHADOW_ONLY-Flip | Hard-Resolution-Rate ≥15%? Posterior-Stabilität? | ? |

**Default:** Defer um weitere 7d, wenn Trigger nicht erfüllt. Aktivierung nur bei klarem Empirie-Anchor.

## 6. Decision Phase 2

_Zu befüllen am 2026-05-23:_

Drei Optionen mit explizitem Argumentationsteil:

- **A) Active-Mode aktivieren** (SHADOW_ONLY → false): nur wenn ALLE Gates + Posterior-Stabilität + Hard-Resolution-Rate ≥15% + Sentiment-Drift adressiert.
- **B) Shadow-Phase verlängern um weitere 7d** (bis 2026-05-30): wenn Daten dünn, aber Pfad zur Sufficient-Stabilität sichtbar.
- **C) Re-Entry-Konzept überdenken** (Konstruktive Eskalation): wenn Sentiment-Drift fundamentaler ist und der bestehende Bridge-Filter (`{bullish, bearish}` only) selbst als Designproblem identifiziert wird.

Decision **muss** begründet sein (KAI-Master §9 + §10), nicht nur Status quo fortschreiben.

## 7. Verantwortlichkeiten + Nächste Schritte

_Zu befüllen am 2026-05-23:_

- Operator-Action-Items (mit Stichtag).
- Claude-Action-Items (Sprint-Definition + Stichtag).
- Subagent-Aktivierung (z.B. SATOSHI für Live-Mode-Vorbereitung wenn Phase-A, oder Architecture-Red-Team bei Phase-C).
- Memory-Pin-Updates erforderlich (z.B. `[[re-entry-decision-2026-05-16]]` schließen, neue Phase-2-Pin anlegen).

---

## Provenance

- **Datenquellen Gate-Messung:** Pi `/home/kai/ai_analyst_trading_bot/artifacts/alert_outcomes.jsonl` + `paper_execution_audit.jsonl` + `bayes_*audit.jsonl` + `source_confluence_audit.jsonl`.
- **Related Memos:**
  - `re_entry_decision_2026-05-16.md` (Eröffnungsentscheidung).
  - `re_entry_adr_cluster_2026-05-17.md` (6 ADRs + ADR-1-Reversion-Block 2026-05-19).
  - `paper_min_priority_decision_2026-05-14.md` (Pin-Stand vor ADR-1).
- **Daily-Strategy-Anchor:** `artifacts/daily_strategy/2026-05-23.md` (entsteht durch `daily-strategy.timer` morgens 08:00 CEST am 23.).
- **Pi-HEAD bei Fill:** zum Befüllungs-Zeitpunkt verifizieren.

---

## Pre-Fill Snapshot 2026-05-20 (Mid-Window-Forensik)

**Zweck:** Daten heute schon sichern, damit der 23.05.-Fill nicht alle Zahlen neu erheben muss. Diese Sektion ist deskriptiv (was wir heute gemessen haben), nicht entscheidend (keine ADRs werden hier ratifiziert).

### Pi-Live-Status 2026-05-20 ~20:30 CEST

- `git rev-parse HEAD` = `f96f74fa` (== `origin/claude/p7/reentry-ia-codex-cycle`, unverändert seit 19.05. 21:47).
- `/health` → ok, `/health/premium_pipeline` → healthy=true (Live-Smoke 2026-05-20 20:30 CEST).
- Services: 5/5 aktiv (kai-server, kai-tg-listener, kai-entry-watch, cloudflared, kai-agent-worker) + 9 Timer waiting.
- `.env`: `EXECUTION_PAPER_MIN_PRIORITY=10` (ADR-1 reverted 19.05. 21:34 CEST), `RISK_BAYES_CONFIDENCE_SHADOW_ONLY=true`, `RE_ENTRY_MODE_ENABLED=true`, `LIVE_MODE=disabled`.

### Bayes-Audit-Stream-Bewegung seit ADR-1-Reversion

| Zeitpunkt | `bayes_confidence_audit.jsonl` | Quelle des neuen Eintrags |
|---|---|---|
| 2026-05-17 23:50 (ADR-1 active, =8) | 2 Einträge (beide 2026-05-15) | — |
| 2026-05-19 21:34 (Reversion, =10) | 2 Einträge | — |
| 2026-05-20 18:18:57 UTC | **3 Einträge** | **NEU:** `dec_b646432da8c0`, BTC/USDT long, cryptoslate-News „CME launching VIX-style fear trade", posterior 0.964, evidence_weight 2.90 |

**Beobachtung:** Erster neuer Bayes-Eintrag seit Phase-2D-E2E-Test (2026-05-15) — entstand ~23h NACH der Reversion auf =10. Das relativiert die im 19er-Reversion-Block formulierte Konsequenz „Priority-Gate ist NICHT der Hebel": Die Audit-Schreibrate ist ≤1/Tag, das 45h-=8-Fenster war damit zu kurz für eine valide Nicht-Wirksamkeitsaussage. Die im 19er ausgearbeitete Sentiment-Pfad-These bleibt aber gültig (s.u.).

### Sentiment-Verteilung im 7d-Fenster (V1-Forensik 2026-05-20)

Stream `alert_audit.jsonl`, gefiltert auf Einträge mit `sentiment_label`-Feld (1826 von 7817 = 23.4%; das Feld wurde nicht von Anfang an mitgeloggt, deshalb die niedrige Coverage über die Gesamthistorie).

**Last 7d (2026-05-13 bis 2026-05-20, n=107):**

| Sentiment | Anteil |
|---|---|
| mixed | 53.3% |
| neutral | 39.3% |
| bullish | 7.5% |
| bearish | 0.0% |
| **directional (bullish+bearish)** | **7.5%** |

**Daily-Trend (n_with_field pro Tag):**

| Datum | n | bullish | bearish | mixed | neutral | directional% |
|---|---|---|---|---|---|---|
| 2026-05-13 | 23 | 1 | 0 | 12 | 10 | 4.3% |
| 2026-05-14 | 27 | 5 | 0 | 12 | 10 | 18.5% |
| 2026-05-15 | 18 | 2 | 0 | 8 | 8 | 11.1% |
| 2026-05-16 | 3 | 0 | 0 | 2 | 1 | 0.0% |
| 2026-05-17 | 11 | 0 | 0 | 8 | 3 | 0.0% |
| 2026-05-18 | 15 | 0 | 0 | 7 | 8 | 0.0% |
| 2026-05-19 | 11 | 0 | 0 | 7 | 4 | 0.0% |
| 2026-05-20 | 18 | 1 | 0 | 10 | 7 | 5.6% (= heutiger Bayes-Eintrag) |

**Befund:** Vier aufeinanderfolgende Tage (16.–19.05.) mit **0% directional** in den klassifizierten Alerts. Bearish ist seit 15.05. komplett verschwunden. Heute der erste bullish-Eintrag seit 4 Tagen (= der eine, der durch alle Gates kam und Bayes-Audit auslöste).

### Priority x Sentiment Cross-Tab (alle 1826 mit-Feld-Einträge)

| Priority-Bucket | n | directional (bull+bear) | distribution |
|---|---|---|---|
| p ≥ 10 | 418 | **182 (43.5%)** | neutral 209, bullish 151, bearish 31, mixed 27 |
| p ≥ 8 (incl. 8,9) | 475 | **417 (87.8%)** | bullish 354, bearish 63, mixed 44, neutral 14 |
| p < 8 | 917 | 287 (31.3%) | neutral 367, mixed 263, bullish 171, bearish 116 |

**Architektur-Befund (NEU heute):** Das p≥10-Gate selektiert **paradoxerweise mehr neutral-Sentiment (50%) als bullish (36%) oder bearish (7%)**. Im Vergleich dazu ist der p=8/9-Bucket zu 87.8% direktional. Mögliche Ursache: Priority=10 wird von "Topic-Relevanz × Asset-Match × Source-Trust" getrieben, nicht von Sentiment-Klarheit — ein Asset-relevanter, neutraler News-Artikel (z.B. Regulatorisches, Listing-Ankündigung) bekommt priority=10, eine direktionale aber Topic-marginale Meldung nur priority=8.

**Implikation für ADR-1:** Die im 19er-Block formulierte Konsequenz „Priority-Gate ist nicht der Hebel" stimmt — aber aus einem anderen Grund als dort vermutet. Der dominante Engpass ist nicht „die Bridge filtert bullish/bearish raus" (das tut sie, aber nur sekundär), sondern „das Priority-Scoring ist mit Sentiment-Klarheit am p≥10-Gate negativ korreliert". ADR-1 auf =8 hätte mehr direktionales Material durchgelassen — aber das war keine valide Reaktion auf das eigentliche Designproblem, sondern eine Symptom-Lockerung.

### Alert-Outcomes-Pipeline (V4-Forensik 2026-05-20)

`alert_outcomes.jsonl`: 8483 Einträge gesamt (+340 seit 17.05.).

| outcome | n | Anteil |
|---|---|---|
| inconclusive | 8101 | 95.5% |
| miss | 255 | 3.0% |
| hit | 127 | 1.5% |

**Backfill-Anteil:** 7688/8101 = **94.9% der inconclusives haben `backfill` im note-Feld** (Auto-Annotate-Pipeline, die Asset-Preisbewegung gegen Threshold prüft).

**Daily-Trend Outcomes:**

| Datum | total | hit | miss | inconclusive | incon% |
|---|---|---|---|---|---|
| 2026-05-14 | 61 | 3 | 1 | 57 | 93.4% |
| 2026-05-15 | 119 | 0 | 3 | 116 | 97.5% |
| 2026-05-16 | 121 | 0 | 1 | 120 | 99.2% |
| 2026-05-17 | 120 | 0 | 0 | 120 | **100.0%** |
| 2026-05-18 | 120 | 0 | 0 | 120 | **100.0%** |
| 2026-05-19 | 115 | 0 | 0 | 115 | **100.0%** |
| 2026-05-20 (bis 20:30 CEST) | 95 | 0 | 0 | 95 | **100.0%** |

**Befund:** Die Hit/Miss-Lernkurve ist seit 2026-05-16 komplett tot. 470 outcomes in 4 Tagen, **alle** inconclusive. Das ist konsistent mit V1: ohne direktionale Sentiment-Klassifizierung kommen keine direktionalen Pre-Tags, also klassifiziert das Auto-Annotate ausschließlich Backfill-incon. Die Source-Reliability-Wilson-Loop hat seit 2026-05-16T12:21 keinen Recalc-Anker mehr (alle 8 erfassten Sources unverändert `insufficient`).

### Regime-Stand 2026-05-20 (BTC + ETH)

Aus `regime_state/btc_regime.jsonl` und `eth_regime.jsonl`, je 1h-Resolution:

| Datum | BTC breakout_up:chop_quiet | ETH breakout_up:chop_quiet |
|---|---|---|
| 2026-05-13 | 8:16 | — |
| 2026-05-15 | 11:13 | — |
| 2026-05-17 | 15:9 | — |
| 2026-05-19 | 19:5 | — |
| 2026-05-20 (bis 19:00 UTC) | 13:7 | 13:7 (analog) |

**Befund:** Regime stabil bullish-leaning seit Re-Entry-Tag. `breakout_up` zunehmend dominant. vol_class durchgängig `vol_low` (rv_24h ~0.0038). Das Marktumfeld selbst ist also kein Erklärungsfaktor für die Sentiment-Drift — wenn überhaupt, müsste in einem trendigen Markt die direktionale Sentiment-Rate steigen, nicht fallen.

### Konsequenz-Updates für Sektionen 2–4 (zur Vorlage am 23.05.)

- **Sektion 2 (Gates):** Resolved directional alerts unverändert 382 (127 hit / 255 miss) seit 2026-05-17 — Auto-Annotate-Pipeline klassifiziert seit 16.05. ausschließlich inconclusive. Baseline-Precision 33.2% eingefroren.
- **Sektion 3 (Lernstack):** V4 Bayes-Audit hat +1 Eintrag (heute). V1 Source-Reliability seit 16.05. unverändert. V2 R3-Shadow läuft (Regime-Daten sauber). V3 Source-Confluence-Audit nicht heute geprüft (kann beim 23.05.-Fill ergänzt werden, war im 19er-Block bei 40 Einträgen).
- **Sektion 4 (Sentiment-Drift):** Vier-Tage-Null-Direktional-Periode 16.–19.05. ist quantifiziert (siehe Daily-Trend oben). Hypothese (a) RSS-Quellen-Wechsel — nicht plausibel (gleiche Quellen, kontinuierlicher Throughput). Hypothese (b) Klassifikator-Threshold-Drift (PR #45/#46/#47-Verdacht aus 19er-Block) — bleibt offen, Code-Read auf Klassifikator-Logik im Sentiment-Mapper steht aus. Hypothese (c) Pipeline-Pfad-Ausfall — widerlegt, Pipeline läuft (heute 18 Alerts, gestern 11, 19. November 11).
- **NEUER Befund Sektion 4 (P0 für 23.05.):** Das **p≥10-Priority-Gate ist negativ mit Sentiment-Klarheit korreliert** (43.5% directional bei p≥10 vs 87.8% bei p=8/9). Das ist potenziell ein Architektur-Bug im Priority-Scoring, nicht nur ein Sentiment-Klassifikator-Problem. **Wenn beim 23.05.-Fill diese Korrelation persistiert, ist eine Code-Inspektion des Priority-Scorings (gewichteter Bonus für Topic/Asset/Trust vs. Sentiment) zwingend, bevor irgendein ADR-Trigger evaluiert wird.**

### Datenquellen für diesen Pre-Fill

- `artifacts/alert_audit.jsonl` (7817 Einträge, davon 1826 mit `sentiment_label`-Feld).
- `artifacts/alert_outcomes.jsonl` (8483 Einträge).
- `artifacts/bayes_confidence_audit.jsonl` (3 Einträge).
- `artifacts/regime_state/btc_regime.jsonl`, `eth_regime.jsonl`.
- `monitor/source_reliability.json` (Snapshot 2026-05-16T12:21:45).
- Verifikationsaufrufe: `git rev-parse HEAD`, `/health/premium_pipeline`, `systemctl list-units --state=active`.


---

## Pre-Fill Snapshot 2026-05-21 (Mid-Window-Forensik Tag 2)

**Zweck:** Zweiter Snapshot zwei Tage vor Fill. Dokumentiert die Pi-Bewegung seit 2026-05-20 20:30 CEST (7 Commits, neue Trail-Observability, Gate 4.5 Code-deployed). Keine ADR-Ratifizierung — nur Snapshot + Verweis auf 5 Memos der Session.

### Pi-Live-Status 2026-05-21 ~09:00 CEST

- `git rev-parse HEAD` = `1226353c` (== `origin/claude/p7/reentry-ia-codex-cycle`).
- **+7 Commits seit 2026-05-20 20:30 CEST** (chronologisch älteste→neueste):
  - `5ff02d8` docs(operator-memos): ADR-1 update 2026-05-20 — priority-sentiment correlation paradox
  - `61de61b` docs(operator-memos): NEW-2 sentiment-classifier drift-check (hypothesis a confirmed, b+c refuted)
  - `b6ea231` docs(daily-strategy): close NEW-1 + NEW-2 on 2026-05-20
  - `c583922` feat(observability): premium-signal end-to-end trail API (/goal 2026-05-20)
  - `235c95d` feat(ui): Premium-Signal Trail panel — end-to-end pipeline visibility
  - `07a86b2` feat(execution): bridge gate 4.5 — scale-plausibility validation (IRYS-Bug)
  - `d2a3e73` chore(risk): raise max_open_positions schema cap 3->6 (operator-decision /goal 2026-05-20)
  - `1226353` feat(observability): render orphan target-completions in trail
- `/health/premium_pipeline` → healthy=true (6/6 checks); `bridge_audit_last_event` 38549s alt (`stale_but_not_a_failure`, älter als Gate-4.5-Deploy 20:07 UTC).
- Services: 5/5 aktiv (kai-server, kai-tg-listener, kai-entry-watch, kai-paper-trading, kai-agent-worker) + 9 Timer.
- `.env` unverändert seit 19.05.: `EXECUTION_PAPER_MIN_PRIORITY=10`, `RISK_BAYES_CONFIDENCE_SHADOW_ONLY=true`, `RE_ENTRY_MODE_ENABLED=true`, `LIVE_MODE=disabled`.

### Bayes-Audit-Stream Bewegung seit Pre-Fill 20.05.

| Zeitpunkt | `bayes_confidence_audit.jsonl` | Quelle des neuen Eintrags |
|---|---|---|
| 2026-05-20 18:18:57 UTC | 3 Einträge | (Pre-Fill 20.05. Stand) |
| 2026-05-21 04:14:07 UTC | **4 Einträge** | **NEU:** `dec_25599792322e`, BTC/USDT long, posterior 0.957, evidence_weight 2.70, news_relevance +1.8 dominant |

**Beobachtung:** Zweiter organischer Bayes-Eintrag seit ADR-1-Reversion 19.05. 21:34 CEST (= 2 organisch / 5 Tage = 0.40/Tag). Identisches Pattern wie 20.05.-Eintrag: BTC/USDT long, hohe posterior, news_relevance dominant, kein Symbol/Direction-Diversitätssignal. Detail-Bewertung in [[bayes-decision-basis-2026-05-21]].

### Heute (2026-05-21) Mid-Window-Sprint — 5 neue Memos

| ID | Titel | Memo | Status |
|---|---|---|---|
| DS-20260521-V1 | EOW-Decision-Pack Priority-Scoring | `priority_scoring_decision_brief_2026-05-23.md` (93 Z) | done |
| DS-20260521-V2 | Premium-Signal-Trail-Validierung | (Daily-Eintrag, kein eigenes Memo — API+Bundle+Orphan-Source verifiziert) | done |
| DS-20260521-V3 | Bridge Gate 4.5 Wirksamkeits-Audit | `bridge_gate_4_5_audit_2026-05-21.md` (62 Z) | done |
| DS-20260521-V4 | max_open_positions 3->6 Impact-Window | `max_open_positions_impact_check_2026-05-21.md` (51 Z) | done |
| DS-20260521-V6 | Bayes-Audit-Trend-Bewertung | `bayes_decision_basis_2026-05-21.md` (76 Z) | done |

### Konsequenz-Updates für Sektionen 2-6 (zur Vorlage am 23.05.)

- **Sektion 1 (Pi-Live-Status):** HEAD `1226353c` (zu verifizieren am 23.05.). Services + Timer komplett, Healthcheck grün. `.env` unverändert.
- **Sektion 2 (Re-Entry-Gates):** Resolved directional alerts weiter 382 (127/255), inconclusive-Rate weiter 95.5% — siehe 20.05.-Pre-Fill, kein Delta heute. Bayes 4 (+1).
- **Sektion 3 (Lernstack):** V4 Bayes-Audit +1 (4 total, beide organischen Einträge BTC/USDT long). V1-V3 unverändert seit Pre-Fill 20.05. — Detail-Trend-Bewertung in [[bayes-decision-basis-2026-05-21]] §3-§5.
- **Sektion 4 (Sentiment-Drift):** Architektur-Befund Priority-Scoring vs Sentiment ist heute durch Voll-Inspection in [[priority-scoring-inspection-2026-05-20]] + Decision-Brief [[priority-scoring-decision-brief-2026-05-23]] vollständig adressiert. **Operator-Decision steht am 23.05. an** (4 Optionen A/B/C/D, Empfehlung Claude: Variante 1 = Option D bis 30.05.).
- **Sektion 5 (ADR-Re-Eval):** ADR-6 SHADOW_ONLY-Flip — Empfehlung NICHT flippen (Datenbasis n=4 zu dünn, Flip-Bedingung n>=20 ODER >=4 Wochen, siehe [[bayes-decision-basis-2026-05-21]] §5). ADR-1 — Status quo solange Priority-Scoring-Decision offen.
- **Sektion 6 (Decision Phase 2):** Drei Decision-Optionen wie im Skeleton vorgesehen. **Operator-Sign-off 2026-05-21 (Pre-Review): Option B ratifiziert** — Shadow-Phase +7d bis 2026-05-30, gekoppelt an Priority-Scoring-Decision-Variante 1 (Option D) aus [[priority-scoring-decision-brief-2026-05-23]]. Begründung: Bayes-Datenbasis zu dünn für A, fundamentale C-Re-Konzeption greift zu weit ohne Priority-Scoring-Sign-off vorab. EOW-Review 23.05. wird damit zur Validierungs+Snapshot-Sitzung (nicht Decision-Sitzung); nächste Decision-Pflicht 2026-05-30.

### Neue Observability + Execution-Hygiene (heute deployed)

- **Premium-Signal-Trail** (commits `c583922` + `235c95d` + `1226353`): End-to-End-API `GET /api/premium-signals/trail` (HTTP 200 mit Bearer), 50-Window-Verteilung 27 NOT_APPROVED / 8 CLOSED / 6 BRIDGE_REJECTED / 4 UNKNOWN / 3 EXPIRED / 2 SOURCE_SKIPPED. UI-Component `PremiumSignalTrail.tsx` in `Portfolio.tsx` mounted, Bundle-Hash `index-CczzKNpT.js`. Orphan-Pipeline aktiv (`target_completion_audit.jsonl` mit 7 `orphan_no_match` Einträgen). **Trail wird beim 23.05.-Fill als primäres Verifikations-Werkzeug genutzt** — Operator kann pro Premium-Signal die 6 Pipeline-Stages live einsehen.
- **Bridge Gate 4.5** (commit `07a86b2`): `scale_resolver.validate_scaled_signal()` mit 7 strukturierten Reasons (`scale_collapses_to_zero`, `long_sl_at_or_above_entry|spot`, `short_sl_at_or_below_entry|spot`, `long_targets_at_or_below_entry`, `short_targets_at_or_above_entry`). Seit Deploy 20:07 UTC: 0 Treffer (Erklärung: kein neues Signal durch Bridge in 11h). Code-Korrektheit über 9 Unit-Tests im Commit abgesichert. Re-Audit beim ersten Live-Trigger.
- **max_open_positions 3->6** (commit `d2a3e73`): Cap-Bump deployed-aber-ungenutzt. Post-Deploy ~9h11min: 1 buy-fill (BTC long, stop-out), max concurrent=1. Re-Audit nach 7d oder bei >=5 buy-fills.

### Datenquellen für diesen Pre-Fill

- `artifacts/bayes_confidence_audit.jsonl` (4 Einträge, letzter 2026-05-21T04:14:07 UTC).
- `artifacts/paper_execution_audit.jsonl` (268 events, schema v2).
- `artifacts/target_completion_audit.jsonl` (2497 B, 7 orphan_no_match).
- `GET /api/premium-signals/trail?limit=50` (Live-API).
- `/health/premium_pipeline` (`bridge_audit_last_event` Feld).
- `git log f96f74f..HEAD` (7 Commits).
- Verifikationsaufrufe: `git rev-parse HEAD`, `systemctl list-units kai-*`, `wc -l` auf Audit-Streams.
- 5 Memos: `priority_scoring_decision_brief_2026-05-23.md`, `bridge_gate_4_5_audit_2026-05-21.md`, `max_open_positions_impact_check_2026-05-21.md`, `bayes_decision_basis_2026-05-21.md`, plus Daily `artifacts/daily_strategy/2026-05-21.md`.
