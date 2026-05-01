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
#
# The script assumes the KAI checkout lives at /home/kai/ai_analyst_trading_bot
# (path is hard-coded in the unit files). If you deploy elsewhere, edit the
# units first or use a bind-mount.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="${REPO_ROOT}/deploy/systemd"
UNIT_DST="/etc/systemd/system"

UNITS=(
    "kai-server.service"
    "kai-agent-worker.service"
    "kai-tg-listener.service"
    "cloudflared.service"
    "kai-paper-trading.service"
    "kai-paper-trading.timer"
    "kai-daily-strategy.service"
    "kai-daily-strategy.timer"
    "kai-daily-strategy-reminder.service"
    "kai-daily-strategy-reminder.timer"
)

ENABLE_ON_INSTALL=(
    "kai-server.service"
    "kai-agent-worker.service"
    "kai-tg-listener.service"
    "cloudflared.service"
    "kai-paper-trading.timer"
    "kai-daily-strategy.timer"
    "kai-daily-strategy-reminder.timer"
)

DRY_RUN=0
UNINSTALL=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --force) FORCE=1 ;;
        -h|--help)
            sed -n '3,17p' "$0"
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

uninstall() {
    require_root
    echo "Stopping + disabling KAI units…"
    for unit in "${UNITS[@]}"; do
        run systemctl stop "$unit" || true
        run systemctl disable "$unit" || true
        run rm -f "${UNIT_DST}/${unit}"
    done
    run systemctl daemon-reload
    echo "Uninstall complete."
}

install() {
    require_root
    echo "Source:      $UNIT_SRC"
    echo "Destination: $UNIT_DST"
    echo ""

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
        run install -m 0644 "$src" "$dst"
    done

    run systemctl daemon-reload

    echo ""
    echo "Enabling units so they start at boot…"
    for unit in "${ENABLE_ON_INSTALL[@]}"; do
        run systemctl enable --now "$unit"
    done

    echo ""
    echo "Done. Verify with:"
    echo "  systemctl status kai-server kai-agent-worker kai-tg-listener cloudflared"
    echo "  systemctl list-timers 'kai-*'"
    echo "  curl -s http://127.0.0.1:8000/health"
    echo "  journalctl -u kai-tg-listener -n 30  # MTProto connect should show 'channel listener live'"
}

if (( UNINSTALL == 1 )); then
    uninstall
else
    install
fi
