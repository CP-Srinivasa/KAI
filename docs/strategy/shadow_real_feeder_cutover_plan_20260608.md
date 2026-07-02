# Cutover-Plan: Read-only Real-Analysis-Feeder (NEO-P-002-r3)

- Datum: 2026-06-08
- Status: **PLAN** — nichts wird durch dieses Dokument aktiviert. Kein Runtime-Eingriff, kein Deploy.
- Flag: `EXECUTION_SHADOW_REAL_GENERATOR` (Default **False**, `app/core/settings.py`)
- Treiber: `app/observability/shadow_real_feed.py::run_shadow_real_feed`
- Ziel: den echten `SignalGenerator` auf **realen** analysierten Dokumenten messen (Shadow-Kandidaten, `source=autonomous_generator`), damit `edge_report`/`shadow_candidate_ledger` echte Samples auflösen können — **ohne jede Execution**.

## 0. Deploy-Hygiene (p7→Pi, VOR allem anderen)

Der Feeder-Flip setzt einen sauberen p7→Pi-Deploy voraus. Dieser Deploy ist
**vom Feeder-Flip getrennt** und folgt der Build-/Release-Disziplin:

- **Isolierter Worktree**: Deploy-Artefakte aus einem dedizierten, sauberen
  Worktree/Checkout des p7-Tips bauen — **nicht** im aktiven Entwicklungs-Tree
  (verhindert die wiederholten Branch-Drift-/Parallel-Session-Vorfälle).
- **Kein Frontend-Build auf der Pi**: `web/`-Bundle **lokal** bauen
  (`npm ci && npm run build`), das fertige `web/dist/` zur Pi shippen. Die Pi
  baut **kein** Frontend (kein `npm` auf der Pi, kein Node-Toolchain-Drift, kein
  RAM-/CPU-Spike auf dem Trading-Host).
- **Rollback-`dist`**: vor dem Überschreiben das aktuelle `web/dist/` auf der Pi
  sichern (`web/dist.bak_<ts>`), damit das Frontend unabhängig vom Backend
  zurückrollbar ist.
- **Backend-Deploy**: Code via isoliertem Worktree → Pi (regulärer
  `pi_install_systemd.sh --reactivate`-Pfad), Services kontrolliert neu starten.

Erst wenn der Deploy grün ist (Abschnitt 4 Smokes), wird über den Feeder-Flip
(Abschnitt 5) entschieden — **nie** im selben Schritt.

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

## 4. Post-Deploy Smoke (Feeder noch OFF, read-only)

Läuft **nach** dem p7→Pi-Deploy (Abschnitt 0) und **vor** dem Feeder-Flip — der
Feeder ist hier weiterhin OFF. Alles read-only.

**4a — Backend-Smoke**
```bash
curl -fsS localhost:8000/health | head        # 200
systemctl --state=failed                       # 0 loaded units
journalctl -u kai-server --since "-5min" | grep -iE "error|traceback" | head  # leer erwartet
```

**4b — Frontend-Smoke** (kein Build auf der Pi — nur das geshippte `dist` prüfen)
```bash
test -f web/dist/index.html && echo "dist present"
curl -fsS localhost:8000/dashboard | grep -qi "<div id=\"root\"" && echo "SPA served"
# Browser/Operator: Dashboard lädt, keine 404 auf Assets
```

**4c — Fastlane-OFF-Smoke** (Invariante: Fastlane bleibt aus, kein Bypass scharf)
```bash
grep -E 'PREMIUM_FASTLANE_ENABLED|PREMIUM_PAPER_EXECUTION_ENABLED|EXECUTION_ENTRY_MODE' .env
#   erwartet: FASTLANE=false, PAPER=false, ENTRY_MODE=disabled
curl -fsS localhost:8000/api/premium-signals/runtime | \
  python -c 'import sys,json; d=json.load(sys.stdin)["premium_fastlane"]; print("enabled",d["enabled"],"overrides",d["overrides_classic_block"])'
#   erwartet: enabled False, overrides_classic_block False
```

**4d — D-227-Smoke** (Dispatch-Recall-Proxy lebt — Regression-Guard)
```bash
# Dispatch-Recall-Proxy (D-227) liefert weiter Recall-Metriken, nicht degeneriert
curl -fsS "localhost:8000/dashboard/api/quality" | \
  python -c 'import sys,json; d=json.load(sys.stdin); print("resolved", d.get("resolved_count"), "precision", d.get("precision_pct"))'
#   erwartet: konsistente Werte, kein Crash / kein Null-Kollaps
```

**4e — Eligibility-Probe** (Go/No-Go für den Feeder selbst)
```bash
python -m app.cli ... eligibility-probe   # etablierter Probe-Command (#184)
#   erwartet >0 eligible; 0 → Feeder hätte nichts zu tun (No-Go, kein Flip)
```

**4f — Baseline-Snapshots** (für Delta-Vergleich nach Feeder-ON)
```bash
wc -l artifacts/paper_execution_audit.jsonl artifacts/shadow_candidate_ledger.jsonl
# Positions-Snapshot sichern
```

Go-Kriterien (ALLE): 4a health 200 ∧ 0 failed; 4b dist present ∧ SPA served;
4c Fastlane/Paper false ∧ entry_mode disabled ∧ overrides False; 4d D-227 nicht
degeneriert; 4e eligible > 0. Ein „nein" → **kein** Feeder-Flip.

## 5. Real-Feeder ON (separate, explizite Phase — Flag-Flip + Restart)

**Bewusst getrennt von OFF**: Abschnitt 0–4 lassen den Feeder OFF (Status quo).
Erst hier, als eigene Operator-Entscheidung nach grünem Smoke, wird `…ON`
gesetzt. OFF und ON sind nie im selben Schritt.

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

Zwei unabhängige Ebenen — Feeder und Deploy getrennt rollbar:

**7a — Feeder-Rollback** (häufigster Fall, env-only, sofort)
```bash
# Flag zurück auf false (oder .env-Backup zurückspielen)
#   EXECUTION_SHADOW_REAL_GENERATOR=false
cp ~/kai-deploy-backups/env_pre_feeder_on_<ts>.bak .env   # alternativ
sudo systemctl restart kai-server kai-tg-listener kai-agent-worker
systemctl --state=failed   # 0 erwartet
```

**7b — Frontend-Rollback (`dist`)** (unabhängig vom Backend)
```bash
# das vor dem Deploy gesicherte dist zurückspielen
rm -rf web/dist && mv web/dist.bak_<ts> web/dist
# kein Rebuild auf der Pi — nur das alte Bundle zurück
```

**7c — Backend-Code-Rollback** (Git-Pin auf den letzten guten p7-Stand)
```bash
# auf den vorherigen p7-Deploy-Pin zurück (aus isoliertem Worktree gebaut)
#   git checkout <previous-p7-pin> + scripts/pi_install_systemd.sh --reactivate
```

OFF stellt den Status quo her (nur der `loop_control_*`-Canary speist den
Shadow-Pfad). Feeder-Rollback ist rein Env-gesteuert und reversibel.

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
