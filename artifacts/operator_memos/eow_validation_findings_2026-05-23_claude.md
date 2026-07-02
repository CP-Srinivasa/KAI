# EOW Validation Findings — Claude Track (2026-05-23)

**Erstellt:** 2026-05-23 (Claude-Session, parallel zur V1-EOW-Memo-Session).
**Zweck:** Datensammlung + Konsistenz-Befunde für V1-EOW-Review-Memo. Disjoint zu `re_entry_end_of_window_2026-05-23.md` (parallele Session füllt dort).
**Drift-Schutz:** Append-only; mtime der V1-Datei NICHT berührt.

---

## 1. Pi-Live-Validierung 2026-05-23 ~08:35 UTC

| Item | Wert | Pin-Vergleich |
|---|---|---|
| HEAD | `eea3582a` | +2 Codex-Docs-Commits ahead von Pin `66494aa7` |
| Branch | `claude/p7/reentry-ia-codex-cycle` sync mit `origin` | unverändert |
| `/health` | `ok` | unverändert |
| `/health/premium_pipeline` | `healthy=true`, 6/6 checks | unverändert |
| `bridge_audit_last_event` | `2026-05-21T13:14:41 UTC` (~43h alt) | `stale_but_not_a_failure` (erwartbar) |
| Active services | 4 (server, tg-listener, entry-watch, agent-worker) | unverändert |
| Timer | 9 aktiv | unverändert |
| `.env` Flags | `EXECUTION_PAPER_MIN_PRIORITY=10`, `RISK_BAYES_CONFIDENCE_SHADOW_ONLY=true`, `RE_ENTRY_MODE_ENABLED=true`, `RISK_MAX_OPEN_POSITIONS=6` | identisch zu Pin |

**Codex-Commits seit Pin:** `767f02a` (V5-followup reporting-split spec) + `eea3582` (arch/onboarding F-1..F-5 drift fix) — beide docs-only, kein Code-Risiko.

---

## 2. Re-Entry-Gates Delta 17.05. → 23.05.

### Lifetime-Snapshot

| Metrik | 17.05. Baseline | 23.05. Live | Δ |
|---|---|---|---|
| Resolved directional alerts (lifetime) | 382 (127/255) | **385 (128/257)** | +3 (+1 hit / +2 miss) |
| Inconclusive-Rate (lifetime) | 95.3% (7761/8143) | **95.7% (8485/8870)** | +0.4 pp (steigt) |
| Baseline-Precision (lifetime) | 33.2% | **33.2%** | unverändert (128/385 = 33.247%) |

### 7d-Fenster (Source: at-Job EOW-Snapshot 07:00 UTC, `/mnt/kai-data/eow_snapshots/`)

| Metrik | 7d-Wert |
|---|---|
| `alert_outcomes` 7d total | **913** |
| davon `inconclusive` | **909 (99.6%)** ⚠ |
| davon `miss` | 3 (0.3%) |
| davon `hit` | 1 (0.1%) |
| davon mit `backfill`-note | 865 (94.7%) |

**Bewertung:** Lifetime-Precision-Stabilität ist Artefakt — über das 7d-Fenster sind nur **4 directional** resolved (1 hit / 3 miss) bei 913 Outcomes (99.6% inconclusive). Lernkurve seit 16.05. tot bestätigt; Pipeline-A-Reaktivierung 21.05. 13:21 hat 48h später noch keine messbare Wirkung gegen Pipeline-B-Dominanz.

### Paper-Trades

Daily-Skeleton-Counter zeigt 8 (stale lokal). **Pi-Source-of-Truth** (parallele Session): `position_closed=15` + `position_partial_closed=24`. Re-Entry-Gate ≥10 erfüllt.

---

## 3. Lernstack V1-V4.1 — Stand 23.05.

| Modul | Stream | 17.05. | 23.05. | Bewertung |
|---|---|---|---|---|
| V1 Source-Reliability | `source_reliability.json` | 8 insufficient | (unchanged file existiert) | Wilson-Loop läuft, aber 0 ratings → keine Source hat Sample erreicht |
| V2 R3-Shadow Regime | `regime_audit.jsonl` | ? | (timer aktiv hourly) | Read-only Stamping läuft |
| V3 Source-Confluence | `source_confluence_audit.jsonl` | 40 (alle count=0) | **40 (mtime 16.05.)** | seit Bridge Gate 4.5-Deploy 0 Treffer |
| V4 Bayes-Posterior | `bayes_posterior_audit.jsonl` | 8 | **8 unchanged** | kein neuer recalc |
| V4 Bayes-Confidence | `bayes_confidence_audit.jsonl` | 3 | **4** | +1 organisch (21.05. 04:14 UTC, BTC long, posterior 0.957) |

**Bayes-Diversität für Flip-Heuristik:**
- n_total=4 (Symbol/Side-Pairs nur 2: XRP/USDT long + BTC/USDT long)
- 3x BTC/USDT long, 1x XRP/USDT long → **keine bearish/short-Diversität**
- Schreibrate seit ADR-1-Reversion (19.05.): 0.40/Tag organisch
- Heuristik-Bedingung [[kai-bayes-shadow-only-flip-heuristik]]: **n>=20 ODER >=4 Wochen + Symbol/Direction-Diversität + 7d-Sentinel** — keine Bedingung erfüllt
- **Empfehlung:** SHADOW_ONLY NICHT flippen am 30.05. — n+Diversität-Pfad ist mathematisch nicht erreichbar bis dahin (selbst bei 5x Schreibrate-Verdopplung).

---

## 4. Sentiment-Distribution-Drift — VERSCHÄRFUNG seit 20.05.

**7d-Fenster 16.-23.05. (Source: at-Job EOW-Snapshot 07:00 UTC, 105 alerts mit sentiment_label):**

| Bucket | n | directional% | bullish/bearish/neutral/mixed |
|---|---|---|---|
| p ≥ 10 | 8 | **25.0%** | bullish 2 / bearish 0 / neutral 1 / mixed 5 |
| p = 8/9 | 12 | **8.3%** | bullish 1 / bearish 0 / neutral 2 / mixed 9 |
| p < 8 | 85 | **0.0%** | bullish 0 / bearish 0 / neutral 43 / mixed 42 |

**Vergleich zur Mid-Window-Forensik 20.05. (1826 alerts, alle bisherigen):**

| Bucket | 20.05. directional% (Pin) | 23.05. 7d directional% | Δ |
|---|---|---|---|
| p ≥ 10 | 43.5% | 25.0% | **-18.5 pp** |
| p = 8/9 | 87.8% | 8.3% | **-79.5 pp** ⚠ |
| p < 8 | 31.3% | 0.0% | **-31.3 pp** |

**Befund:** Das Paradox aus 20.05. ist überlagert von einem **fundamentalen Sentiment-Drift**. p=8/9-Band kollabiert von 87.8% auf 8.3% directional. **0 bearish in 7d** (Pin 20.05. nannte schon "4-Tage-Null-direktional-Periode 16.-19.05." — heute auf 7 Tage verlängert mit nur 3 bullish gesamt).

**Konsequenz für Priority-Scoring-Decision 2026-05-30:**
- Option A' (PR #58 sentiment-penalty -2 auf neutral/mixed) verliert empirische Grundlage — bei 2.9% directional in 7d bleiben nach Penalty fast keine Alerts übrig, die das Gate passieren.
- Option D (Status quo bis 30.05.) bleibt korrekt, ABER mit **neuem Risiko:** Wenn Sentiment-Drift bis 30.05. anhält, ist auch 30.05.-Decision auf 7d-Daten unmöglich (Sample zu dünn).
- **Eskalation:** Sentiment-Drift-Forensik priorisiert vor 30.05. notwendig (Quellen-Drift, Klassifikator-Threshold, oder echte Markt-Phase).

---

## 5. Trail-API + Premium-Channel-Stille + max_open_positions

- Trail-API `GET /api/premium-signals/trail?limit=20` → HTTP 200, count=20, jüngster `2026-05-21T13:14 UTC BEAT/USDT CLOSED`.
- `premium_signal_actions.jsonl`: total=15, **7d=+1** (last `2026-05-20T21:50:20 UTC`) → **53h kein neues Premium-Signal**.
- `bayes_confidence_audit.jsonl`: 4 total, 7d=+2 (beide pre-21.05.), **53h kein neuer Eintrag** seit `dec_25599792322e` (21.05. 04:14 UTC).
- `max_open_positions=6` deployed-aber-ungenutzt: max concurrent=1 in der Pi-Beobachtung post-Deploy. Re-Audit-Schwelle (>=5 buy-fills oder 7d echte Signal-Volumen) **nicht erreicht** — Cap-Bump empirisch noch nicht validiert.

**Schreibrate-Trajektorie:** Bayes seit 13.05. = 4 Einträge / 10 Tage = **0.40/Tag**, jüngstes 7d-Fenster = 2/7 = **0.29/Tag** (fallend). n≥20-Schwelle bei 0.29/Tag erst **~2026-08-23**, bei 0.40/Tag erst **~2026-06-23**. Flip-Heuristik n>=20-Pfad ist faktisch tot — Zeit-Komponente (>=4 Wochen) wird vor n-Komponente erreicht (2026-06-13).

---

## 6. Konsistenz-Befund DS-V1..V6 (21.05.)

| ID | Aussage | Live-Check 23.05. | Status |
|---|---|---|---|
| V1 | Decision-Brief 4 Optionen + PR #58 A' | `priority_scoring_decision_brief_2026-05-23.md` 7KB + `priority_scoring_inspection_2026-05-20.md` 12.4KB vorhanden | OK konsistent |
| V2 | Trail-API validiert | API HTTP 200, 20 Einträge | OK konsistent |
| V3 | Bridge Gate 4.5 0 Treffer post-deploy | `source_confluence_audit.jsonl` mtime 16.05., 40 Z, alle count=0 | OK konsistent (by-design stumm) |
| V4 | max_open_positions deployed, max concurrent=1 | unchanged, Cap-Bump weiter ungenutzt | OK konsistent |
| V5 | EOW-Skeleton 227→294 Z, `.bak_20260521` vorhanden | Backup-File 13.7KB, aktuelles File 20.7KB | OK konsistent |
| V6 | Bayes n=4, 0.40/Tag organisch, SHADOW_ONLY=true | n=4 unchanged, .env-Flag aktiv | OK konsistent |

Alle 6 Memos vom 21.05. sind 2 Tage später noch valide. Keine Stale-Pin-Korrektur nötig.

---

## 7. Empfehlung für V1-EOW-Memo

1. **23.05. ist Snapshot-Sitzung** (Operator-Sign-off 21.05.) — keine Decision-Ratifikation.
2. **Sentiment-Drift muss als P0-Folge-Sprint** vor 30.05. eingeplant werden — sonst kollabiert auch die 30.05.-Decision auf zu dünner Datenbasis. **Spec angelegt:** `artifacts/operator_memos/sentiment_drift_p0_forensik_spec_2026-05-23.md`.
3. **SHADOW_ONLY-Flip 30.05. nicht möglich am n-Pfad** — bei aktueller Schreibrate (0.29-0.40/Tag) erreicht der Stream `n>=20` erst Juni-August. Zeit-Pfad (>=4 Wochen) reißt vor n-Pfad (Schwelle 2026-06-13).
   **Konkrete Empfehlung Flip-Heuristik-Update:** [[kai-bayes-shadow-only-flip-heuristik]] um Klausel ergänzen — `n>=10 (statt n>=20) wenn Zeit-Komponente >=6 Wochen UND Symbol-Direction-Diversitaet >=4 Pairs UND keine n>=3 posterior>0.9-misses im 14d-Sentinel`. Begründung: bei sehr niedriger Schreibrate ist n>=20 mathematisch tot ohne dass das Risiko-Bild dadurch besser wird; Zeit + Diversität + Sentinel kompensieren niedrigeres n. Operator-Sign-off vor Patch.
4. **Cap-Bump max_open=6 weiter im Re-Audit-Backlog** — Re-Audit-Schwelle nicht erreicht.
5. **Snapshot-Generator `.env`-Block korrigiert** 2026-05-23 11:07 CEST (commit pending): Legacy-Keys `SHADOW_ONLY` + `RE_ENTRY_MODE` entfernt, korrekte Keys `RE_ENTRY_MODE_ENABLED` + `LIVE_MODE` ergänzt. Snapshot-File `priority_scoring_eow_snapshot_2026-05-23.md` (`/mnt/kai-data/eow_snapshots/`) zeigt jetzt `RISK_BAYES_CONFIDENCE_SHADOW_ONLY=true`, `RE_ENTRY_MODE_ENABLED=true`, `RISK_MAX_OPEN_POSITIONS=6`, `LIVE_MODE=<unset>` (legitim, .env hat keinen LIVE_MODE-Key, Code-Default `disabled`).

---

## Datenquellen (für V1-Memo zitierbar)

- `git rev-parse HEAD` → `eea3582a`
- `GET /health/premium_pipeline` (alle 6 Checks ok)
- `wc -l artifacts/bayes_confidence_audit.jsonl` → 4
- `wc -l artifacts/alert_audit.jsonl` → 7865 (gefiltert dispatched_at>=2026-05-17: 103 mit sentiment_label)
- `wc -l artifacts/alert_outcomes.jsonl` → 8870 (8485 inconclusive, 128 hit, 257 miss)
- `wc -l artifacts/bayes_posterior_audit.jsonl` → 8 unchanged
- `wc -l artifacts/source_confluence_audit.jsonl` → 40 (mtime 16.05.)
- `GET /api/premium-signals/trail?limit=20` → count=20, jüngster 21.05.
- `cat .env` → 4 Flags konsistent zu Pin

---

**Cross-Link für V1-EOW-Memo:** Diese Datei bei Sektion 1-4 zitieren, dort verweisen auf `eow_validation_findings_2026-05-23_claude.md` §1-§7. Source-of-Truth-Hierarchie: at-Job EOW-Snapshot (`/mnt/kai-data/eow_snapshots/priority_scoring_eow_snapshot_2026-05-23.md`, 07:00 UTC) > Pi-Live-API > diese Findings-Datei.
