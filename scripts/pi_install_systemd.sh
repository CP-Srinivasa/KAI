#!/usr/bin/env bash
# Install KAI systemd units on the Raspberry Pi.
#
# D-190 / NEO-F-META-20260424-005 — replaces the copy-paste workflow in
# docs/pi_migration/preflight.md §6 with a reproducible install step so the
# 2026-05-01 cutover is single-chance-proof.
#
# Usage (on the Pi, after git clone + venv + .env):
#     sudo bash scripts/pi_install_systemd.sh            # install + enable + start
#     sudo bash scripts/pi_install_systemd.sh --dry-run  # show what would happen
#     sudo bash scripts/pi_install_systemd.sh --uninstall
#     sudo bash scripts/pi_install_systemd.sh --force    # skip path-warning prompt
#                                                          (SSH non-interactive; D-208)
#     sudo bash scripts/pi_install_systemd.sh --no-enable # install + daemon-reload only,
#                                                          # do NOT enable/start units.
#                                                          # Cutover pre-stage: keeps the
#                                                          # new host idle so it does not
#                                                          # race the old host (e.g.
#                                                          # cloudflared/Telegram-Session
#                                                          # single-instance constraints).
#
# The script assumes the KAI checkout lives at /home/kai/ai_analyst_trading_bot
# (path is hard-coded in the unit files). If you deploy elsewhere, edit the
# units first or use a bind-mount.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="${REPO_ROOT}/deploy/systemd"
UNIT_DST="/etc/systemd/system"
TMPFILES_SRC="${REPO_ROOT}/deploy/tmpfiles/kai.conf"
TMPFILES_DST="/etc/tmpfiles.d/kai.conf"

UNITS=(
    "kai-server.service"
    "kai-agent-worker.service"
    "kai-tg-listener.service"
    "cloudflared.service"
    "kai-paper-trading.service"
    "kai-paper-trading.timer"
    "kai-entry-watch.service"
    "kai-regime-classify.service"
    "kai-regime-classify.timer"
    "kai-premium-healthcheck.service"
    "kai-premium-healthcheck.timer"
    "kai-health-check.service"
    "kai-health-check.timer"
    "kai-parser-feedback.service"
    "kai-parser-feedback.timer"
    "kai-premium-latency-audit.service"
    "kai-premium-latency-audit.timer"
    "kai-daily-strategy.service"
    "kai-daily-strategy.timer"
    "kai-daily-strategy-reminder.service"
    "kai-daily-strategy-reminder.timer"
    "kai-pi-health.service"
    "kai-pi-health.timer"
    "kai-service-watchdog.service"
    "kai-service-watchdog.timer"
    "kai-hold-report.service"
    "kai-hold-report.timer"
    "kai-auto-annotate.service"
    "kai-auto-annotate.timer"
    "kai-recalc-cycle.service"
    "kai-recalc-cycle.timer"
)

ENABLE_ON_INSTALL=(
    "kai-server.service"
    "kai-agent-worker.service"
    "kai-tg-listener.service"
    "cloudflared.service"
    "kai-paper-trading.timer"
    "kai-entry-watch.service"
    "kai-regime-classify.timer"
    "kai-premium-healthcheck.timer"
    "kai-health-check.timer"
    "kai-parser-feedback.timer"
    "kai-premium-latency-audit.timer"
    "kai-daily-strategy.timer"
    "kai-daily-strategy-reminder.timer"
    "kai-pi-health.timer"
    "kai-service-watchdog.timer"
    "kai-hold-report.timer"
    "kai-auto-annotate.timer"
    "kai-recalc-cycle.timer"
)

# 2026-05-14: Reactivate-Hook — kritische Premium-Signal-Pipeline-Units.
# Hintergrund: Beim 2026-05-12-Deploy blieben kai-paper-trading.timer und
# kai-entry-watch.service nach systemctl-Stop inaktiv (Restart-Limit getriggert
# durch transienten Mid-Deploy-Config-Mismatch). Operator merkte den Ausfall
# 48h lang nicht — Bridge-Tick fehlte, Premium-Signale liefen 10h Delay bis Fill.
# Die hier gelisteten Services sind die, deren Inaktivität eine stille Pipeline-
# Degradation verursacht (Symptome erst im Audit-Log sichtbar, nicht in /health).
CRITICAL_REACTIVATE=(
    "kai-server.service"
    "kai-tg-listener.service"
    "kai-paper-trading.timer"
    "kai-entry-watch.service"
    "kai-premium-healthcheck.timer"
    "cloudflared.service"
)

DRY_RUN=0
UNINSTALL=0
FORCE=0
NO_ENABLE=0
REACTIVATE_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --force) FORCE=1 ;;
        --no-enable) NO_ENABLE=1 ;;
        --reactivate) REACTIVATE_ONLY=1 ;;
        -h|--help)
            sed -n '3,24p' "$0"
            exit 0
            ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

run() {
    echo "+ $*"
    if (( DRY_RUN == 0 )); then
        "$@"
    fi
}

require_root() {
    if (( EUID != 0 )); then
        echo "ERROR: must run as root (use sudo)" >&2
        exit 1
    fi
}

# Post-install / post-deploy smoke: verify each critical unit is active.
# Inactive units get one reset-failed + restart attempt. Final state is reported
# per unit so a stale restart-counter (siehe entry-watch counter 1091, 2026-05-12)
# cannot leave a service silently dead after an otherwise-successful deploy.
# Exit code 0 = all reactivated; exit code 1 = at least one still inactive.
#
# 2026-05-14 Fix: 'activating' wird als healthy akzeptiert. Hintergrund:
# kai-entry-watch.service hat `--duration-seconds 55` → Service zykelt zwischen
# 'active' (running) und 'activating' (restarting). `systemctl is-active --quiet`
# returnt nur bei state=active exit 0; bei state=activating exit 3 → ein
# false-positive Restart wurde getriggert, obwohl der Service gesund war.
# `systemctl is-active <unit>` (ohne --quiet) liefert den state als string;
# wir akzeptieren active+activating+reloading (Symmetrie zu
# premium_pipeline_health._HEALTHY_ACTIVE_STATES).
_is_healthy_active_state() {
    local state="$1"
    case "$state" in
        active|activating|reloading) return 0 ;;
        *) return 1 ;;
    esac
}

reactivate_critical() {
    local failed=0
    echo ""
    echo "=== Reactivate-Hook: verifying critical services ==="
    for unit in "${CRITICAL_REACTIVATE[@]}"; do
        local state
        state="$(systemctl is-active "$unit" 2>/dev/null || true)"
        if _is_healthy_active_state "$state"; then
            echo "  OK    $unit (state=$state)"
            continue
        fi
        echo "  WARN  $unit state=$state — reset-failed + restart"
        run systemctl reset-failed "$unit" 2>/dev/null || true
        run systemctl restart "$unit" || true
        # Give the unit a moment to settle; entry-watch + paper-trading
        # complete one cycle in <30s, so 5s is enough for liveness check.
        sleep 5
        state="$(systemctl is-active "$unit" 2>/dev/null || true)"
        if _is_healthy_active_state "$state"; then
            echo "  OK    $unit (recovered, state=$state)"
        else
            echo "  FAIL  $unit state=$state — manual diagnosis required"
            failed=$((failed + 1))
        fi
    done
    if (( failed > 0 )); then
        echo ""
        echo "Reactivate-Hook: $failed critical unit(s) still inactive."
        echo "Investigate with: journalctl -u <unit> -n 100"
        return 1
    fi
    echo "Reactivate-Hook: all critical units active."
    return 0
}

uninstall() {
    require_root
    echo "Stopping + disabling KAI units…"
    for unit in "${UNITS[@]}"; do
        run systemctl stop "$unit" || true
        run systemctl disable "$unit" || true
        run rm -f "${UNIT_DST}/${unit}"
    done
    run systemctl daemon-reload
    if [[ -f "$TMPFILES_DST" ]]; then
        echo "Removing tmpfiles config…"
        run rm -f "$TMPFILES_DST"
        # Note: do NOT call `systemd-tmpfiles --remove` here — that would delete
        # /home/kai/ai_analyst_trading_bot/logs/ and any rotated log archives.
        # The directory is intentionally preserved on uninstall so log forensics
        # remain available.
    fi
    echo "Uninstall complete."
}

install() {
    require_root
    echo "Source:      $UNIT_SRC"
    echo "Destination: $UNIT_DST"
    echo ""

    # D-208: web/dist (Vite SPA build) is .gitignored. The Pi-4b 1GB-RAM
    # variant cannot run `npm ci + tsc + vite build` reliably (OOM/SSH
    # banner timeouts under memory pressure). The build is done on the
    # laptop instead via `scripts/pi_deploy_web.sh` which scp's the
    # tarball. Run that BEFORE `pi_install_systemd.sh` if web/dist is
    # absent or stale.
    if [[ ! -f "${REPO_ROOT}/web/dist/index.html" ]]; then
        echo "WARNING: web/dist/index.html missing — /dashboard/ will return 404." >&2
        echo "         Run on the laptop: bash scripts/pi_deploy_web.sh ubuntu@192.168.178.20" >&2
        echo ""
    fi

    # Basic pre-flight — verify the checkout path matches the unit files.
    EXPECTED_ROOT="/home/kai/ai_analyst_trading_bot"
    if [[ "$REPO_ROOT" != "$EXPECTED_ROOT" ]]; then
        echo "WARNING: repo root is $REPO_ROOT but units point at $EXPECTED_ROOT." >&2
        echo "         Either checkout at $EXPECTED_ROOT or edit units first." >&2
        if (( DRY_RUN == 0 && FORCE == 0 )); then
            # FORCE=1 oder non-interactive (SSH ohne TTY) erfordern --force.
            # Auf Pi mit /home/kai → /home/ubuntu Symlink ist die Pfad-Diskrepanz
            # erwartet — D-208 Cutover Lessons-Learned.
            read -r -p "Continue anyway? [y/N] " answer
            [[ "$answer" == "y" || "$answer" == "Y" ]] || exit 1
        fi
    fi

    for unit in "${UNITS[@]}"; do
        src="${UNIT_SRC}/${unit}"
        dst="${UNIT_DST}/${unit}"
        if [[ ! -f "$src" ]]; then
            echo "ERROR: missing source unit: $src" >&2
            exit 1
        fi
        run command install -m 0644 "$src" "$dst"
    done

    # 2026-05-07 Cutover-Lehre B-3: kai-server-Erststart auf Blank-Slate
    # crashte mit `Failed to set up standard output: No such file or directory`
    # weil systemd StandardOutput=append: VOR ExecStartPre oeffnet. Tmpfiles
    # legt logs/ vor jedem Service-Start an (via systemd-tmpfiles-setup,
    # laeuft vor multi-user.target).
    if [[ ! -f "$TMPFILES_SRC" ]]; then
        echo "ERROR: missing tmpfiles source: $TMPFILES_SRC" >&2
        exit 1
    fi
    echo ""
    echo "Installing tmpfiles config (logs/-Verzeichnis-Bootstrap)…"
    run command install -m 0644 "$TMPFILES_SRC" "$TMPFILES_DST"
    run systemd-tmpfiles --create "$TMPFILES_DST"

    run systemctl daemon-reload

    if (( NO_ENABLE == 1 )); then
        echo ""
        echo "--no-enable: units are installed but NOT enabled or started."
        echo "Activate later with:"
        for unit in "${ENABLE_ON_INSTALL[@]}"; do
            echo "  sudo systemctl enable --now $unit"
        done
        echo ""
        echo "Done (install-only)."
        return
    fi

    echo ""
    echo "Enabling units so they start at boot…"
    for unit in "${ENABLE_ON_INSTALL[@]}"; do
        run systemctl enable --now "$unit"
    done

    if (( DRY_RUN == 0 )); then
        reactivate_critical || true
    fi

    echo ""
    echo "Done. Verify with:"
    echo "  systemctl status kai-server kai-agent-worker kai-tg-listener cloudflared"
    echo "  systemctl list-timers 'kai-*'"
    echo "  curl -s http://127.0.0.1:8000/health"
    echo "  journalctl -u kai-tg-listener -n 30  # MTProto connect should show 'channel listener live'"
    echo "  journalctl -u kai-service-watchdog -n 30"
}

# Standalone-Aufruf: bash scripts/pi_install_systemd.sh --reactivate
# Ruft NUR den Reactivate-Hook auf, ohne Re-Install. Für Post-Deploy-Smoke
# nach `git pull && systemctl restart kai-server` ohne kompletten Reinstall.
reactivate_only() {
    require_root
    reactivate_critical
}

if (( REACTIVATE_ONLY == 1 )); then
    reactivate_only
    exit $?
elif (( UNINSTALL == 1 )); then
    uninstall
else
    install
fi
