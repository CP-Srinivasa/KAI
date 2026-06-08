# Cutover-Plan: Read-only Real-Analysis-Feeder (NEO-P-002-r3)

- Datum: 2026-06-08
- Status: **PLAN** — nichts wird durch dieses Dokument aktiviert. Kein Runtime-Eingriff, kein Deploy.
- Flag: `EXECUTION_SHADOW_REAL_GENERATOR` (Default **False**, `app/core/settings.py`)
- Treiber: `app/observability/shadow_real_feed.py::run_shadow_real_feed`
- Ziel: den echten `SignalGenerator` auf **realen** analysierten Dokumenten messen (Shadow-Kandidaten, `source=autonomous_generator`), damit `edge_report`/`shadow_candidate_ledger` echte Samples auflösen können — **ohne jede Execution**.

## 1. Was der Feeder ist — und was nicht

- **Ist**: ein read-only Mess-/Shadow-Pfad. Replayt reale Analysen durch die bestehende `run_trading_loop_once(mode=SHADOW, analysis_result=…)`-Naht; der Loop schreibt im `entry_mode=disabled`-Shadow-Pfad einen *hypothetischen* Kandidaten.
- **Ist nicht**: ein Execution-Pfad. **Kein** Fill, **keine** Order, **keine** Position, **kein** Paper/Probe/Live.
- **Keine Fastlane-Verknüpfung** (BINDEND): Der Feeder hat mit Premium-Fastlane **nichts** zu tun und darf nie mit ihr gekoppelt, in einem PR vermischt oder als Begründung für ein Fastlane-Re-Enable benutzt werden. Fastlane bleibt OFF (D-231/D-232, ADR 0006). Zwei getrennte Welten: Feeder = Messung; Fastlane = Execution.

## 2. Hard-Invarianten (während + nach Cutover)

| Invariante | Sollwert | Verifikation |
|---|---|---|
| `EXECUTION_ENTRY_MODE` | `disabled` | `.env` + Runtime-Status |
| `PREMIUM_FASTLANE_ENABLED` | `false` | `.env` + Runtime-Status |
| `PREMIUM_PAPER_EXECUTION_ENABLED` | `false` | `.env` + Runtime-Status |
| Fills (heute) | `0` | `paper_execution_audit.jsonl` Delta |
| Offene Positionen | unverändert | positions-snapshot vor/nach |
| Orders | `0` | bridge/exec-audit Delta |
| Feeder-Modus | `SHADOW` (hart) | Code-Invariante `_default_run_once` |
| LIVE | hart blockiert (Triple-Flag) | unverändert |

Eine einzige verletzte Invariante ⇒ **sofort Rollback** (Abschnitt 6).

## 3. Precondition-Gate (kein Feeder-ON ohne diese)

Der Feeder darf **nicht** scharfgeschaltet werden, solange nicht **alle** erfüllt sind:

1. **#185 in p7** (fail-closed Fastlane-Bypass-Defaults + Override-Preflight) — *erfüllt* (squash `2086b442`). Stellt sicher, dass selbst bei versehentlich gesetzten Fastlane-Flags `disabled` weiter `disabled` bedeutet.
2. **#189 in p7** (In-Loop-Funnel-Achsen) — Pflicht, **damit ein `real_resolved=0` erklärbar ist** (priority/sentiment/generator-none/…) statt still als `EDGE_NEGATIVE` gelesen zu werden. Ohne diese Instrumentierung ist der Feeder blind und wird **nicht** aktiviert.
3. **Dieser Smoke-Plan** (Abschnitt 4–5) ist abgearbeitet und grün.
4. p7 ist auf die Pi deployt (regulärer Deploy-Pfad), CI grün, `systemctl --state=failed` = 0.

Solange (1)–(4) nicht alle gelten: Feeder bleibt OFF.

## 4. Pre-Cutover Smoke (read-only, vor dem Flag-Flip)

Alles read-only — verändert nichts:

```bash
# Auf der Pi (ubuntu@192.168.178.23), Repo-Root, venv aktiv:
# a) Invarianten-Ist-Zustand
grep -E 'EXECUTION_ENTRY_MODE|PREMIUM_FASTLANE_ENABLED|PREMIUM_PAPER_EXECUTION_ENABLED|EXECUTION_SHADOW_REAL_GENERATOR' .env

# b) Health + Failed-Units
curl -fsS localhost:8000/health | head
systemctl --state=failed

# c) Eligibility-Probe (Go/No-Go: liefern reale Analysen überhaupt Kandidaten?)
#    erwartet mind. >0 eligible; 0 → Feeder hätte nichts zu tun (No-Go, kein Flip)
python -m app.cli ... eligibility-probe   # bzw. der etablierte Probe-Command (#184)

# d) Baseline-Snapshots (für Delta-Vergleich nach Cutover)
wc -l artifacts/paper_execution_audit.jsonl artifacts/shadow_candidate_ledger.jsonl
#    Positions-Snapshot sichern
```

Go-Kriterien: a) entry_mode=disabled ∧ beide Premium-Flags false; b) health 200 ∧ 0 failed units; c) eligible > 0.

## 5. Cutover (Flag-Flip + kontrollierter Restart)

```bash
# .env-Backup ZUERST
cp .env ~/kai-deploy-backups/env_pre_feeder_on_$(date +%Y%m%d_%H%M%S).bak

# Flag setzen — NUR diese eine Zeile; Fastlane/Premium/entry_mode NICHT anfassen
#   EXECUTION_SHADOW_REAL_GENERATOR=true
# (kontrolliert editieren, nicht andere Flags mitziehen)

# Kontrollierter Restart der betroffenen Services
sudo systemctl restart kai-server kai-tg-listener kai-agent-worker
```

Erste Feeder-Ticks abwarten (der Treiber läuft im bestehenden Zyklus/Cron).

## 6. Post-Cutover Verification (T+0, T+1h, T+24h)

```bash
# 1) Invarianten weiter gehalten?
grep -E 'EXECUTION_ENTRY_MODE|PREMIUM_FASTLANE_ENABLED|PREMIUM_PAPER_EXECUTION_ENABLED' .env   # unverändert
systemctl --state=failed                                                                       # 0

# 2) KEINE Execution entstanden?
#    Fills heute == 0, offene Positionen unverändert vs Baseline, 0 Orders
#    (paper_execution_audit Delta zeigt NUR shadow/record-Events, keine order_filled)

# 3) Feeder wirkt + In-Loop-Funnel erklärt (das #189-Delta):
tail -n 5 artifacts/shadow_real_feed_funnel.jsonl   # enthält jetzt "in_loop"-Block
#    erwartet: real_analyses_seen > 0, by_cycle_status dominiert von entry_mode_blocked
#    (= shadow_candidate_written) und/oder generator_returned_none; rejected_funnel erklärt Rest

# 4) Shadow-Report bleibt ehrlich:
#    real_resolved klein/0 => primary_class == INSUFFICIENT_DATA (nie EDGE_NEGATIVE),
#    rejected_funnel surfaced (build_shadow_report(..., inloop_funnel=…))
```

Akzeptanz: Invarianten gehalten (0 Fills/Orders/Positionen-Delta), Funnel zeigt reale Generator-Aktivität, Report bleibt `INSUFFICIENT_DATA` bis genug real_resolved.

## 7. Rollback (jederzeit, sofort bei Invarianz-Bruch)

```bash
# Flag zurück auf false (oder .env-Backup zurückspielen)
#   EXECUTION_SHADOW_REAL_GENERATOR=false
cp ~/kai-deploy-backups/env_pre_feeder_on_<ts>.bak .env   # alternativ
sudo systemctl restart kai-server kai-tg-listener kai-agent-worker
systemctl --state=failed   # 0 erwartet
```

OFF stellt den Status quo her (nur der `loop_control_*`-Canary speist den Shadow-Pfad). Kein Git-Rollback nötig — rein Env-gesteuert, reversibel.

## 8. Bewusst nicht in diesem Plan

- Kein Premium-Fastlane-/Premium-Paper-Re-Enable (separater #181-Merge-Gate, ADR 0006).
- Kein `entry_mode`-Wechsel weg von `disabled`.
- Keine neue Edge-Infra; der Feeder nutzt ausschließlich bestehende Recording-/Resolver-Schichten.
- Kein Auto-Schedule des Flag-Flips — Aktivierung ist eine bewusste Operator-Aktion nach erfülltem Gate (Abschnitt 3).

## Cross-Ref

- D-231 / D-232 (Fastlane OFF + Merge-Gate), ADR 0006.
- #185 (fail-closed Kill-Switch), #189 (In-Loop-Funnel — Voraussetzung), #184 (Eligibility-Probe).
- `app/observability/shadow_real_feed.py`, `shadow_inloop_funnel.py`, `shadow_candidate_ledger.py`.
- Memory: `neo_p002_r3_sprint_prep`, `kai_edge_verdict_canary_probe_artifact`.
