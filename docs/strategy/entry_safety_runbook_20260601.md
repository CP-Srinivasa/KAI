# Entry-Safety-Mode + Edge-Release Runbook (Goal 2026-06-01)

Operator-Kurzanleitung für die D-229-Schicht. Volle Begründung: `DECISION_LOG.md` D-229.

## Was das ist

- `EXECUTION_ENTRY_MODE` ist der **runtime kill-switch** für autonome Loop-Entries.
  Werte: `disabled` | `paper` | `probe` | `live_limited` | `live_normal`. Default: `paper`.
- `trading edge-report` und `trading edge-gate` sind **read-only Diagnose/Entscheidungs-Tools**.
  Sie ändern `EXECUTION_ENTRY_MODE` NICHT. `edge-gate` gibt nur eine *Empfehlung* + Begründung aus.
- Hochstufung in einen Live-Mode ist **immer** eine manuelle Operator-Aktion mit Sign-off.
  Es gibt keinen Auto-Promote. `live_normal` wird nie automatisch gesetzt.

## edge-report + edge-gate auf der Pi laufen lassen

Read-only, kein Eingriff in die Runtime. Audit-Pfad = der laufende paper-Audit-Stream.

```bash
# Auf der Pi (ubuntu@192.168.178.23), im Repo-Root, venv aktiv:

# 1) Kosten-bereinigte Edge-Diagnostik (pro Symbol/Regime/Tag, P(mu_net>0), Churn, MTM)
python -m app.cli.main trading edge-report \
  --audit-path artifacts/paper_execution_audit.jsonl \
  --venue paper

# 2) Release-Verdict (DISABLED/PAPER/PROBE/LIVE_LIMITED/LIVE_NORMAL + Begründung)
python -m app.cli.main trading edge-gate \
  --audit-path artifacts/paper_execution_audit.jsonl \
  --venue paper --min-n 20 --oos-min-days 2

# JSON für Automatisierung/Logging:
python -m app.cli.main trading edge-gate --audit-path artifacts/paper_execution_audit.jsonl --json
```

`--min-n` = minimale Zahl closed round-trips, bevor ein Verdict verteidigbar ist (Default 20).
`--safety-margin-bps` = zusätzliche bps, die ein Cohort über 0 liegen muss, bevor ein Live-Vorschlag
überhaupt erwogen wird. `--oos-min-days` = disjunkte qualifizierende Tages-Cohorts für LIVE_NORMAL.

Verdict-Leiter (gegen die Edge-Verteilung): `P<0.5` oder `n<min_n` → **DISABLED**;
`0.5≤P<0.8` (mit realem net-Edge) → **PAPER/PROBE**; `0.8≤P<0.95` → **LIVE_LIMITED** (Sign-off);
`P≥0.95` UND OOS-stabil → **LIVE_NORMAL** eligible (Sign-off, nie auto).

## entry_mode hochstufen (nur per Operator-Sign-off)

1. `edge-gate` laufen lassen. Wenn der Verdict KEIN Live-Mode ist → **nicht** hochstufen.
2. Wenn der Verdict einen Live-Mode empfiehlt: das ist eine Empfehlung, kein Auftrag.
   Plausibilität prüfen (n, P, net bps/notional, OOS-Breakdown im Render).
3. Erst dann manuell setzen — Live-Modes verlangen zusätzlich `EXECUTION_MODE=live`
   (fail-closed: ein Live-Entry-Mode auf paper-Venue wird beim Settings-Load abgelehnt):

   ```bash
   # Pi .env (NICHT in git) — Wert ändern, dann Service neu starten:
   EXECUTION_ENTRY_MODE=live_limited   # niemals direkt live_normal ohne edge-gate-Evidenz
   # EXECUTION_MODE=live               # nur wenn echte Live-Venue freigegeben ist
   sudo systemctl restart kai-server
   ```

4. Post-Change: `edge-gate` bleibt das laufende Kontrollinstrument; bei Verschlechterung
   der Verteilung sofort auf `disabled` oder `paper` zurück (siehe Rollback).

> **Regel:** `live_normal` wird ausschliesslich nach mehreren disjunkten qualifizierenden
> Tagen (OOS-stabil) und explizitem Operator-Sign-off gesetzt — nie reflexartig, nie automatisch.

## Rollback

Vollständig über Env, keine Migration, kein irreversibler Eingriff:

```bash
EXECUTION_ENTRY_MODE=disabled   # stoppt alle autonomen Loop-Entries sofort
# oder zurück auf den Status quo:
EXECUTION_ENTRY_MODE=paper
sudo systemctl restart kai-server
```

Churn-Killer separat entschärfen (alle 0 = inert):
`RISK_CHURN_MAX_TRADES_PER_SYMBOL_PER_HOUR=0`, `RISK_CHURN_MAX_NOTIONAL_TURNOVER_PER_HOUR=0`,
`RISK_CHURN_COOLDOWN_MIN=0`.

## Garantie

Exit- und Risk-Management (`monitor_positions` / `close_position` / Stops) werden **nie**
vom Entry-Gate oder Churn-Killer blockiert. Auch bei `EXECUTION_ENTRY_MODE=disabled` und
maximal aggressiver Churn-Konfiguration stoppt eine offene Position aus. Getestet in
`tests/unit/test_trading_loop_churn_killer.py::test_hard_invariant_exits_never_blocked_by_churn`
und `tests/unit/test_goal_acceptance_20260601.py::test_step2_churn_rejects_entry_while_exit_still_de_risks`.

## Premium-Fastlane: OFF + Re-Enable-Gate (D-231 / D-232, Issue #181)

Premium-Fastlane-Paper ist die **kanonisch OFF**-Betriebswahrheit:
`PREMIUM_FASTLANE_ENABLED=false` + `PREMIUM_PAPER_EXECUTION_ENABLED=false` auf der
Pi, `EXECUTION_ENTRY_MODE=disabled` unverändert. **Nicht** reaktivieren — weder
per Flag-Flip, `.env`, noch aus Memory.

Seit Issue #181 (PR #185) sind die Bypass-Defaults fail-closed und der
Entry-Mode-Override braucht einen zweiten expliziten Arm; das Enabling der
Fastlane allein hebt den Kill-Switch **nicht** mehr auf. Ein künftiger
Re-Enable ist **nur** über den bindenden Merge-Gate erlaubt — die fünf Pflicht-
Kriterien (bounded operatorlesbarer Modus, fail-closed Preflight, 0-Fill/Order/
Position-Tests, per-source/notional-Limits, reason_codes + Operator-Sichtbarkeit)
stehen in `docs/adr/0006-fastlane-fail-closed-bypass-defaults.md`
§ Re-Enable-Merge-Gate. Fehlt einer → fail-closed, kein Merge, Fastlane bleibt OFF.
