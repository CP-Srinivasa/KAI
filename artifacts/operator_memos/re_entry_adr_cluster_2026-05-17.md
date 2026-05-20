# ADR-Cluster: Re-Entry-Phase-Decisions — 2026-05-17

**Kontext:** Operator hat am 2026-05-17 in der Daily-Strategy die 5 Goal-Sprint-Decisions + paper_min_priority-Decision an Claude delegiert ("Claude-only (alle berechtigungen sind vorhanden)"). Re-Entry-Stichtag 2026-05-16 war D-1, beide formalen Gates erfüllt (siehe `re_entry_decision_2026-05-16.md`).

**Grundsatz:** Konservativer Re-Entry. Lieber 7d Schatten-Daten weiter sammeln als auf dünner Label-Basis (4.7% Hard-Resolution) Active-Switches drehen.

---

## ADR-1: paper_min_priority (V6)

**Aktueller Zustand:** `EXECUTION_PAPER_MIN_PRIORITY=10` (Pin-Stand seit 2026-05-15, D-182-Design).

**Beobachtung:** Bayes-Audit-Stream sammelt nur 2 Einträge in 2 Tagen seit Phase 2D E2E-Validation (2026-05-15). Lernschleife V4 (Bayes-Posterior) bekommt zu wenig Samples für brauchbare Posterior-Updates.

**Decision:** **JA, Lockerung 10 → 8 für 48h-Fenster** (Start 2026-05-17 23:30 CEST, Reversion 2026-05-19 23:30 CEST).

**Begründung:**
- 48h sind kurz genug, um Audit-Akkumulation zu beschleunigen, ohne dass es bei einer Drift-Erkennung lange dauert, zurück zu Default zu kommen.
- Priorität 8 ist immer noch in der oberen Hälfte (Skala 1–10), keine Lockerung auf "alles".
- SHADOW_ONLY bleibt aktiv → keine echte Geld-Exposure-Veränderung.

**Reversion-Plan:**
- Pi `.env` `EXECUTION_PAPER_MIN_PRIORITY=8` setzen, `systemctl restart kai-server kai-paper-trading.timer`.
- Reminder-Task `DS-20260519-REVERSION-V6` mit Stichtag 2026-05-19 23:30 anlegen.
- Bei Hard-Drift (≥3 anomale Paper-Outcomes/24h): sofortige Reversion vor Stichtag.

**Risiko:** Mehr Paper-Trades = mehr Audit-Stream-Volumen = potenziell mehr Pi-Disk-Verbrauch. Aktuell 3.7MB alert_outcomes.jsonl, 2.7KB bayes_confidence_audit.jsonl — Disk-Risiko vernachlässigbar.

---

## ADR-2: Tier-Verteilung

**Kontext:** Aus Goal-Sprint Day 1 Pause-Handover offen. "Tier-Verteilung" referenziert vermutlich Source-Reliability-Tiering (V1 Wilson-Loop) oder Signal-Priorität-Tier.

**Aktueller Zustand:** Alle 8 erfassten Sources stehen auf `insufficient` (V1 Wilson-Loop hat noch keinen Source mit ausreichend Samples für brauchbare Reliability-Schätzung).

**Decision:** **NEIN, kein Tier-Switch jetzt.** Tier-Mapping bleibt Default.

**Begründung:**
- Ohne sufficient-Klassifizierung mindestens einer Source ist eine Tier-Verteilung-Decision spekulativ.
- V7 (Source-Reliability Sufficient-Threshold, P2) muss erst Threshold-Definition liefern.

**Trigger für Wieder-Evaluation:** Sobald ≥3 Sources `sufficient` erreichen oder spätestens End-of-Window-Review 2026-05-23.

---

## ADR-3: R4-Filter-Aktivierung

**Kontext:** R3-Shadow läuft seit 2026-05-16 (PR #51, Goal-Sprint Day 1). R4 wäre der nächste Schritt: Regime-Filter als Active-Gate für TradingLoop.

**Decision:** **NEIN, R4 bleibt gesperrt.**

**Begründung:**
- R3-Shadow läuft erst 1 Tag. Mindestens 7d Beobachtung erforderlich, bevor R4 sinnvoll diskutiert wird.
- Memory-Pin `[[regime-r1-observer-status]]`: 14d Operator-Validation läuft. R3 ist Folge davon, R4 noch weiter.
- Aktivierung ohne Datenbasis verletzt KAI-Master §10 (überprüfbar > vage).

**Trigger:** End-of-Window-Review 2026-05-23 + saubere R3-Shadow-Statistik.

---

## ADR-4: V3-Window (Source-Confluence)

**Kontext:** V3 Source-Confluence shadow audit ist via PR #53 live, läuft seit 2026-05-16. Window-Parameter steuert das Zeitfenster, in dem mehrere Sources als "confluent" gewertet werden.

**Decision:** **NEIN, V3-Window-Default beibehalten.**

**Begründung:**
- Window-Tuning ohne Audit-Daten ist Ratepass.
- Phase-2D Bayes-Audit-Stream hat 2 Einträge — auch V3 hat noch zu wenig konfluente Cases für eine Window-Statistik.

**Trigger:** Mid-Window-Check 2026-05-20 + V3-Audit-Stream ≥10 Cases.

---

## ADR-5: Bayes-Sizing-Aktivierung

**Kontext:** Bayes-Posterior (V4, PR #54) berechnet Confidence pro Signal. Bayes-Sizing wäre, diese Confidence direkt in `position_size_usd` einfließen zu lassen statt in der Decision-Chain stehenzubleiben.

**Decision:** **NEIN, Bayes-Sizing bleibt OFF.**

**Begründung:**
- `[[kai-live-trading-security-phase0]]`: Live-Mode bleibt disabled bis Sprint 39/40/41 grün.
- Bayes-Sizing in Paper hätte zwar keine Geld-Exposure, aber würde Paper-Performance-Signale verschmieren — Paper als Lern-Datengrundlage wäre danach nicht mehr vergleichbar mit Pre-Re-Entry.
- 2 Audit-Einträge sind keine Basis für Posterior-Vertrauen.

**Trigger:** End-of-Window-Review 2026-05-23 + Posterior-Stabilität messbar.

---

## ADR-6: SHADOW_ONLY-Flip

**Kontext:** `SHADOW_ONLY=true` bedeutet: Bayes-Confidence wird berechnet und logged, aber nicht in Decision-Chain eingespeist. Flip auf `false` würde Bayes-Aktiv-Mode bedeuten.

**Decision:** **NEIN, SHADOW_ONLY=true bleibt fix für mindestens 7 Tage** (bis 2026-05-23).

**Begründung:**
- Hard-Resolution-Rate 4.7% (V3-Forensik 2026-05-17) ist zu dünn für Active-Bayes.
- Memory-Pin `[[feedback-kai-no-prediction]]`: KAI darf nicht "predicten" — Bayes-Active in einer dünnen Label-Phase wäre genau das.
- Architektur-Gegencheck (KAI-Master §12): Re-Entry-Window hat noch keine Posterior-Stabilität gemessen.

**Trigger:** End-of-Window-Review 2026-05-23 + Posterior-Stabilität + Hard-Resolution-Rate ≥15% (siehe `re_entry_decision_2026-05-16.md` §3.5).

---

## Zusammenfassung

| ADR | Decision | Stichtag |
|---|---|---|
| ADR-1 paper_min_priority 10→8 | ✅ JA, 48h | Reversion 2026-05-19 23:30 CEST |
| ADR-2 Tier-Verteilung | ❌ Defer | 2026-05-23 End-of-Window |
| ADR-3 R4-Filter | ❌ Defer | 2026-05-23 End-of-Window |
| ADR-4 V3-Window | ❌ Defer | 2026-05-20 Mid-Window |
| ADR-5 Bayes-Sizing | ❌ Defer | 2026-05-23 End-of-Window |
| ADR-6 SHADOW_ONLY-Flip | ❌ Defer | 2026-05-23 End-of-Window |

**Implementierungsschritte ADR-1 (heute):**

1. SSH Pi: `.env` editieren — `EXECUTION_PAPER_MIN_PRIORITY=8`.
2. `systemctl restart kai-server kai-paper-trading.timer`.
3. Reminder anlegen für 2026-05-19 23:30 CEST (Reversion).
4. Memo `paper_min_priority_decision_2026-05-17.md` Carry-over zu `paper_min_priority_decision_2026-05-14.md` schreiben.
5. Daily-Strategy-Progress-Tabelle aktualisieren: DS-20260517-4 → in_progress mit Reversion-Stichtag.

**Operator-Veto:** Wenn Operator eine der Defer-Decisions ratifizieren oder eine andere Decision treffen möchte → einfaches Override-Memo `re_entry_adr_cluster_2026-05-17_override.md` anlegen. Bestehende ADRs bleiben als Audit-Anchor unverändert.

---

## ADR-1 — Reversion + Lessons-Learned (2026-05-19 21:34 CEST)

**Ausgeführt:**

- Pi `.env` Backup `.env.backup_pre_adr1_reversion_20260519T192835` angelegt.
- `EXECUTION_PAPER_MIN_PRIORITY` zurück von `8` auf `10` (sed-Edit Zeile 88, keine inline-comments).
- `sudo systemctl restart kai-server kai-tg-listener kai-entry-watch` — alle 3 Services `active`.
- Smoke: `/health` → ok, `/health/premium_pipeline` → `healthy=true`, 6/6 checks ok.
- Empirie-Fenster: 2026-05-17T21:50 UTC bis 2026-05-19T19:34 UTC (≈45h45min).

**Empirie über das 48h-Fenster:**

| Stream | Zustand Pre-ADR-1 | Zustand Post-Fenster | Δ |
|---|---|---|---|
| `bayes_confidence_audit.jsonl` | 2 Einträge (beide 2026-05-15) | **2 Einträge (beide 2026-05-15)** | **±0** |
| `bayes_posterior_audit.jsonl` | 8 Einträge | 8 Einträge | ±0 |
| `premium_signal_actions.jsonl` | letzter 2026-05-15T12:41 UTC | letzter 2026-05-15T12:41 UTC | ±0 |
| `source_confluence_audit.jsonl` | 40 Einträge | 40 Einträge | ±0 |
| `trading_loop_audit.jsonl` (signal_generated=true) | letzter 2026-05-15T12:19 UTC | letzter 2026-05-15T12:19 UTC | ±0 |
| `trading_loop_audit.jsonl` (cycles 72h) | n/a | 852 cycles, **alle priority_rejected, Priority=1** | — |
| `alert_outcomes.jsonl` | 8143 (127 hit / 255 miss / 7761 inconclusive) | 8363 (127 hit / 255 miss / 7981 inconclusive) | +220 alle `inconclusive` |

**Befund:** Lockerung 10→8 hat **keine messbare Wirkung** auf den Bayes-Audit-Stream. Die Hypothese hinter ADR-1 („Priority-Gate ist der limitierende Faktor") war falsch.

**Root-Cause-Analyse (2026-05-19 V2-Forensik, Pfad C):**

Der `kai-paper-trading.timer` ruft alle ~10min `scripts/paper_trading_cron.sh`, der `python -m app.cli.main trading run-once --analysis-profile conservative` ausführt. Der Conservative-Profile-Analysis-Record hat **by-design `recommended_priority=1`** (`app/orchestrator/trading_loop.py:898`). Code-Kommentar Zeilen 869–876 dokumentiert das explizit: "Conservative stays low (=1) so it remains correctly blocked under the strict gate; bullish/bearish probes are at the high-conviction tier (=10)". Damit ist `priority_gate_reject:1|threshold:8` **erwünschtes Verhalten** für die Control-Plane-Health-Check-Ticks — nicht ein Symptom des Priority-Gate-Werts.

Die ECHTE Signal-Trigger-Brücke ist `_maybe_trigger_paper_trade()` in `app/pipeline/service.py:48–109`. Sie triggert nur, wenn alle vier Bedingungen erfüllt sind:

1. `OPERATOR_SIGNAL_AUTO_RUN_ENABLED=true` — Pi: ✅ true.
2. `sentiment_label ∈ {"bullish", "bearish"}` — letzte 120h: **2 bullish / 0 bearish von 56 Alerts** (3.6%).
3. `affected_assets` nicht leer.
4. mindestens ein Asset zu Symbol auflösbar via `_resolve_symbol()`.

**Tatsächlicher Engpass:** Bedingung 2 — die Sentiment-Verteilung in den letzten 120h ist **22 neutral / 32 mixed / 2 bullish / 0 bearish** (Priority 7×45, 10×6, 9×5). 96.4% der dispatched Alerts sind non-directional und werden von der Bridge by-design verworfen. Selbst wenn ADR-1 auf Priority-Filter zielt, ändert das nichts an der Sentiment-Filter-Hürde, die VORHER greift.

**Konsequenzen:**

1. **ADR-1 hat den falschen Hebel adressiert.** Nicht der Priority-Gate-Wert, sondern die direktionale Klassifikations-Rate ist der limitierende Faktor.
2. **Kein Schaden entstanden.** SHADOW_ONLY=true die ganze Zeit aktiv. Keine echte Markt-Exposure-Veränderung, kein Schaden am Paper-Performance-Vergleich, kein Disk-Risk realisiert.
3. **Neuer P0 für 2026-05-20** (Mid-Window-Check + neue Sprint-Definition): **Sentiment-Distribution-Drift untersuchen.** Warum produzieren AI-Sentiment-Klassifikatoren seit 2026-05-14/15 fast nur `neutral` und `mixed`? Hypothesen:
   - (a) RSS-Feed-Quellen liefern weniger direktionale News (externe Veränderung).
   - (b) Klassifikator-Threshold im Code zu konservativ geworden (PR #45/#46/#47 vom 2026-05-16 als Verdächtige).
   - (c) Pipeline-Pfad RSS → Persist → Analyze ist gar nicht voll aktiv (cron/timer-Lücke).
   Read-only forensik first (`PipelineRunStats` priority_distribution + analyze-Log seit 14.05.), dann gezielt patchen.
4. **End-of-Window-Review 2026-05-23** bleibt geplant, aber Tagesordnung wird umpriorisiert: Sentiment-Drift-Befund wird Hauptthema, nicht Bayes-Posterior-Stabilität (für die sowieso keine Daten anfallen, solange Bridge trocken ist).
5. **Hypothesen-Disziplin schärfen:** Bevor künftig ADRs auf einen vermuteten Hebel gezogen werden, mind. eine Pipeline-Pfad-Verifikation (z.B. „welcher Code-Pfad triggert den Audit-Stream") als Voraussetzung.

**Nächste konkrete Schritte (in Daily-Strategy 2026-05-19/20 zu tracken):**

- DS-20260519-1 → ✅ erledigt (Reversion + Memo-Update, siehe diesen Block).
- DS-20260519-2 → ✅ erledigt (V2-Forensik, Befund hier dokumentiert).
- Neues Backlog-Item **DS-20260520-NEW**: Sentiment-Distribution-Drift Pipeline-Forensik (P0 für 2026-05-20).
- ADR-2..6 (Defer-Decisions): inhaltlich unverändert, aber End-of-Window-Review 2026-05-23 muss um Sentiment-Drift-Status ergänzt werden.

---

## ADR-1 — Update 2026-05-20 (Mid-Window-Befunde)

**Zeitlicher Kontext:** ~23h nach der Reversion auf =10 (19.05. 21:34 CEST → 20.05. 20:30 CEST).

**Neuer Bayes-Eintrag:** `artifacts/bayes_confidence_audit.jsonl` hat heute den **dritten** Eintrag bekommen (`dec_b646432da8c0`, 2026-05-20T18:18:57 UTC, BTC/USDT long, posterior 0.964). Quelle: cryptoslate-News „CME is launching a VIX-style fear trade to bitcoin" (Telegram message_id 1870, sentiment_label=bullish, priority=10, directional_eligible=true). Pipeline-Trace: cryptoslate → alert_audit 18:18:55 → Bayes-Audit 18:18:57 (~2s Latenz).

**Konsequenz für den 19er-Reversion-Block:** Die dort dokumentierte Lesart "Lockerung 10→8 hat keine messbare Wirkung" war richtig für das 45h-Fenster, aber die abgeleitete Konsequenz "Priority-Gate ist nicht der Hebel" muss präziser formuliert werden. Die Audit-Schreibrate ist ≤1/Tag — das 45h-Beobachtungsfenster war zu kurz für eine valide Wirksamkeits- oder Nichtwirksamkeits-Aussage. Was definitiv stimmt: Die Sentiment-Filter-Hürde (Bedingung 2 in `_maybe_trigger_paper_trade`) ist und bleibt der primäre Throttle.

**Neuer Architektur-Befund — Priority x Sentiment Cross-Tab:**

`alert_audit.jsonl` über 1826 Einträge mit `sentiment_label`-Feld:

| Priority-Bucket | n | directional% | distribution |
|---|---|---|---|
| p ≥ 10 | 418 | **43.5%** | neutral 209, bullish 151, bearish 31, mixed 27 |
| p = 8/9 | 475 | **87.8%** | bullish 354, bearish 63, mixed 44, neutral 14 |
| p < 8 | 917 | 31.3% | neutral 367, mixed 263, bullish 171, bearish 116 |

**Das Priority-Scoring ist am p≥10-Gate negativ mit Sentiment-Klarheit korreliert.** Ein Asset-relevanter neutraler News-Artikel (Regulatorisches, Listings, Tech-Updates) kriegt priority=10, eine direktional aber topic-marginal eingestufte Meldung nur priority=8. Das p=8/9-Band ist zu 87.8% direktional — das Material, das ADR-1 mit =8 freigeschaltet hätte, ist tatsächlich direktional-reicher als das p≥10-Material.

**Interpretation:** ADR-1 (=8) wäre nicht die "falsche Lockerung", sondern eine Symptom-Lockerung gegen ein Architektur-Problem im Priority-Scoring. Die Reversion auf =10 ist trotzdem korrekt — nicht weil =8 nichts gebracht hätte, sondern weil das eigentliche Designproblem nicht am Gate-Wert hängt, sondern an der Scoring-Formel davor.

**Aktualisierte Konsequenzen-Liste:**

1. **ADR-1-Reversion korrekt** — aber aus erweitertem Grund: nicht „Lockerung wirkt nicht", sondern „Lockerung wäre Symptom-Behandlung gegen ein Scoring-Problem".
2. **Priority-Scoring-Inspection als P0 für End-of-Window-Review 2026-05-23.** Bevor irgendein neuer ADR auf Gate-Werte zielt, muss die Scoring-Formel selbst untersucht werden (welche Features füttern priority? wo entsteht die Sentiment-Klarheit-Negativ-Korrelation?).
3. **Sentiment-Distribution-Pipeline-Forensik (Hypothesen a/b/c aus 19er-Block) bleibt eigenständig offen.** Beide Befunde — Sentiment-Drift und Priority-Korrelation — sind unabhängig und müssen unabhängig gelöst werden.
4. **Hypothesen-Disziplin (19er-Punkt 5) verschärfen:** ADR-Lockerungen an Gate-Werten ohne Mid-Window-Cross-Tab-Analyse (Priority × Sentiment × Outcome) sind ab sofort unzulässig. Das Cross-Tab heute ist ein Beispiel, wie der eigentliche Engpass aussehen kann (und nicht aussieht).

**Daten-Anhang:**

| Stream | Pre-ADR-1 | Post-Reversion (jetzt) | Δ |
|---|---|---|---|
| `bayes_confidence_audit.jsonl` | 2 (beide 2026-05-15) | **3** (NEU 2026-05-20T18:18:57) | +1 |
| `alert_outcomes.jsonl` | 8143 | 8483 | +340 (alle inconclusive) |
| Hit/Miss-Zähler | 127 / 255 | 127 / 255 | **±0 seit 2026-05-16** |
| `alert_audit` 7d directional% | n/a | **7.5%** (8/107) | siehe EOW-Memo Pre-Fill |
| `alert_audit` Tagesreihe 16.–19. directional% | n/a | **0% / 0% / 0% / 0%** | Vier-Tage-Null-Direktional-Periode |

**Mid-Window-Snapshot vollständig:** Siehe `re_entry_end_of_window_2026-05-23.md` → Sektion „Pre-Fill Snapshot 2026-05-20".

