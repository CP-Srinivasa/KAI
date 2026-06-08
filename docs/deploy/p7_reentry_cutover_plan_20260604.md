# p7-reentry → Pi Cutover-Plan (read-only Vorbereitung, 2026-06-08)

**Status: AKTUALISIERT — Vorbereitung für den Deploy von p7-reentry.**
Entscheidung Operator: Option A (kein Deploy im Freeze). Risikostufe: **HOCH** (Premium-Execution-Code in Produktion, großes Delta).

## Pins
- **Target:** `claude/p7/reentry-ia-codex-cycle` @ `c485c03c5d3f433595e39b855582ecb1451c96ec` — CI **success** (verifiziert 2026-06-08).
- **Known-good Rollback-Pin:** `aef0e61b` (lt. Memory aktueller Pi-Runtime; **am Pre-Snapshot live verifizieren**, nicht blind annehmen).
- Deploy-Scripts: `scripts/pi_install_systemd.sh` (Backend) + `scripts/pi_deploy_web.sh` (Frontend), beide via SSH `ubuntu@192.168.178.23`.

## Delta aef0e61 → c485c03c (37 Commits, 141 Dateien, +18113/−585)
- #142/#143/#144 Dashboard live-tiles + honest badges + vitest
- #145 ci(frontend) vitest · #146 read-only signal-detail endpoints
- #147 Dashboard P0 truth-layer (isolated, no execution code)
- **#149 Premium-Execution-Pipeline** (bridge, paper_engine, reconciler, scale_resolver, settings, state_machine) — Execution-naher Code
- #152 Premium-FE-Wahrheit (Trail-Tone, ExternalSignals-Honesty, Portfolio-Source-Views)
- #150 2 P2 safety-findings (scale band + paper opt-in)
- **#184 Eligibility Probe** (Go/No-Go pre-flight tool)
- **#185 Fastlane fail-closed bypass defaults** (fail-closed settings + preflight override check)
- **#189 Funnel axes** (in-loop funnel metrics for real-analysis feeder)
- **#186 Dashboard metrics registry wiring** (Part A dashboard metrics registry)
- **#190 Ruff lint hygiene** (green ruff checks repo-wide)
- #171 Real-analysis feeder (default OFF), #164 SENTR governance gates, #162 Truth-Layer v2, #161 Generator edge measurement, #168 Cross-exchange price validation, #166 Source reputation engine.

## Vorbedingungen (alle erfüllt, BEVOR Cutover startet)
1. 08.06. Shadow-Report gelaufen + Artefakt gesichert (KAI-mirror\shadow-reports).
2. Report geprüft: `real_resolved`, `primary_class`, `by_source`, `confidence_analysis_status`, `raw_count`/`deduped_count`.
3. Freeze explizit als beendet markiert.
4. Keine aktive Parallel-Session / kein laufender Deploy / keine Locks (Memory-Lehre: andere Session hatte Pi auf #136 gezogen + bewusst zurückgesetzt — solchen Rollback NICHT überfahren).
5. p7 HEAD == c485c03c + CI grün bestätigt.

## Pi Pre-Snapshot (vor jeder Mutation)
- `git rev-parse HEAD` (= erwarteter Rollback-Pin, festhalten)
- `git status --short`
- `systemctl --failed`
- `/health` == 200
- `web/dist` separat sichern (Backup vor Überschreiben — Memory-Lehre pi_deploy_web kein atomic-clean)
- relevante `.env`-Flags festhalten: `EXECUTION_ENTRY_MODE`, `RISK_GATES_MODE`, bridge/allowlist, `PREMIUM_PAPER_EXECUTION_ENABLED`

## Rollout-Reihenfolge
1. **Backend:** `pi_install_systemd.sh --reactivate` auf `c485c03c`.
2. **Backend-Smoke** (siehe unten) — bei Fehler: STOP + Rollback.
3. **Frontend:** Build aus **isoliertem Worktree** (Memory: Pi hat im non-interaktiven Pfad kein node → Laptop-Build; NICHT aus Haupt-Worktree mit ungemergten Tiles bauen), dann `pi_deploy_web.sh`.
4. **Full-Smoke.**

## Smoke-Checks
**Backend:** HEAD==c485c03c · `/health` 200 · 0 failed units · kai-server/kai-agent-worker/kai-tg-listener/kai-entry-watch active.
**Trading/Safety:** `EXECUTION_ENTRY_MODE=disabled` · `RISK_GATES_MODE=audit` · paper book flat · position_count=0 · risk_open_count=0 · keine fills/positions/live-orders · run-once ⇒ entry_mode_blocked/priority_rejected, **keine** Execution.
**Premium/Execution:** Bridge-Kill-Switch global · disabled+Premium-Signal ⇒ Diagnose ja, Fill/Position/Order **nein** · Reconciler/State-Machine startet ohne Mutation · keine Auto-Promotion.
**Frontend:** dist stammt aus `c485c03c` · Dashboard lädt · roadmap/paper_only/live_only-Badges korrekt · Signal-Detail nur funktional wenn Endpoint live.

## Rollback (vor Deploy konkret, nicht theoretisch)
- **Backend:** `git reset --hard <pre-snapshot-SHA>` && `sudo systemctl restart kai-server kai-agent-worker kai-tg-listener kai-entry-watch`
- **Frontend:** `web/dist`-Backup zurückspielen && `sudo systemctl restart kai-server`
- **Known-good:** `aef0e61` bzw. der am Pre-Snapshot verifizierte Pi-Stand.
- **Stop-Kriterien:** failed units > 0, /health ≠ 200, irgendein Fill/Position/Order unter entry_mode=disabled, dist-Hash-Mismatch.

## Post-Deploy-Beobachtung
- T+0/T+30min: /health, systemctl --failed, Logs auf Fehleranstieg, paper book bleibt flat, keine Execution-Events.

## Nicht erlaubt vor Go
git pull/reset auf Pi · systemctl restart · dist-Austausch · pi_install_systemd.sh / pi_deploy_web.sh · entry_mode/gates-Änderung.
