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
#     sudo bash scripts/pi_install_systemd.sh --broker-only # NUR den Broker, keine Unit
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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="${REPO_ROOT}/deploy/systemd"

# Paper-Writer-Freeze-Guard (2026-07-13): gemeinsame Marker-Semantik, damit der
# Reactivate-/Enable-Pfad einen versiegelten Weg-B+-Writer-Freeze respektiert.
# Ursache-Fix zum 2026-07-12-Leak (blindes reset-failed+restart eingefrorener
# Writer → Trade in die kontaminierte Alt-Epoche).
# shellcheck source=scripts/lib/paper_writer_freeze.sh
source "${REPO_ROOT}/scripts/lib/paper_writer_freeze.sh"
# Trennt Provisionierung (frischer Host) von Live-Aenderung (Drift im Ziel).
# shellcheck source=scripts/lib/pi_install_guard.sh
source "${REPO_ROOT}/scripts/lib/pi_install_guard.sh"
PAPER_WRITER_FREEZE_STATE=0  # 0/10/20, je Lauf via _paper_freeze_preflight gesetzt
UNIT_DST="/etc/systemd/system"
TMPFILES_SRC="${REPO_ROOT}/deploy/tmpfiles/kai.conf"
# Helferskript, auf das kai-standby-{data,system}.service per absolutem
# Pfad zeigen. Ohne diesen Schritt installiert ein frischer Host die Units,
# aber nicht ihr ExecStart-Ziel — die Sicherung waere von Anfang an tot.
HELPER_SRC="${REPO_ROOT}/scripts/standby_to_usb.sh"
# Privilegien-Broker (#734). Die sudoers-Policy erlaubt passwortfrei GENAU
# diesen Pfad — er MUSS deshalb existieren, bevor die Policy gilt. Am
# 2026-08-20 war er es nicht: sudoers verwies auf /usr/local/sbin/
# kai-service-control, die Datei fehlte, und damit war JEDER passwortfreie
# privilegierte Pfad tot — inklusive der Auto-Recovery des Service-Watchdogs.
# Eine Policy ohne installiertes Ziel ist keine Sicherheit, sondern ein
# toter Pfad, der als vorhandene Faehigkeit dokumentiert bleibt.
BROKER_SRC="${REPO_ROOT}/deploy/bin/kai-service-control"
BROKER_DST="/usr/local/sbin/kai-service-control"
HELPER_DST="/usr/local/bin/standby_to_usb.sh"
TMPFILES_DST="/etc/tmpfiles.d/kai.conf"

# Kopierliste: ABGELEITET aus deploy/systemd/, nicht handgepflegt.
#
# Bis 2026-08-18 stand hier eine Namensliste. Sie fuehrte 54 von 113 Units —
# 59 waeren auf einem frischen Host NIE installiert worden, darunter die
# Fristen-Uhr (kai-prereg-maturity), die Truth-Verankerung (kai-truth-anchor,
# kai-integrity-anchor) und die Sicherung der Forschungshistorie
# (kai-backup-artifacts). Beim 17.08.-Deploy fielen 15 der 17 zu entmaskierenden
# Units in genau diese Luecke und mussten von Hand nachgezogen werden.
#
# Kopieren ist folgenlos: eine Unit-Datei in /etc/systemd/system tut nichts,
# solange sie nicht enabled ist. Folgenreich ist allein ENABLE_ON_INSTALL
# weiter unten — DIE Liste bleibt handkuratiert (Lehre #626/#627: neue Timer
# nie blind mit-enablen). Template-Units (kai-unit-failure-notify@.service)
# werden dadurch automatisch mitgenommen: sie haengen hinter jedem OnFailure=
# und werden nie enabled, sondern je Fehlschlag instanziiert.
mapfile -t UNITS < <(
    find "$UNIT_SRC" -maxdepth 1 -type f \( -name "*.service" -o -name "*.timer" \) -printf "%f\n" | sort
)
if (( ${#UNITS[@]} == 0 )); then
    echo "ERROR: keine Unit-Dateien in $UNIT_SRC gefunden" >&2
    exit 1
fi

# NOTE 2026-08-06: kai-forecaster-issue.timer + kai-forecaster-resolve.timer
# sind ABSICHTLICH nicht in ENABLE_ON_INSTALL (Lehre #626/#627: neue Timer nie
# blind mit-enablen). Installiert (daemon-reload aware), aber das Scharfschalten
# ist ein bewusster Deploy-Schritt: `systemctl enable --now kai-forecaster-issue.timer
# kai-forecaster-resolve.timer`. Beide sind read-only gegen den Node-losen
# Binance-Daily-Pfad und schreiben nur artifacts/research/forecaster_panel/.

# NOTE 2026-08-06: kai-ln-reconcile.timer ist ABSICHTLICH nicht in
# ENABLE_ON_INSTALL. Der outcome-only Abgleich darf erst nach versiegelter
# Shadow-Prae-Registrierung und explizitem Deploy-Smoke aktiviert werden. Er
# benutzt nur das Read-Credential und sendet/erstellt niemals eine Zahlung.

# NOTE 2026-08-08: kai-ln-reconcile-verdict.timer ist ABSICHTLICH nicht in
# ENABLE_ON_INSTALL (Lehre #626/#627). Er zieht stuendlich das Verdikt der
# versiegelten Prae-Reg 0879a65c5fd01f65, schreibt NUR bei Verdikt-Wechsel nach
# artifacts/research/ln_reconciliation_verdict.jsonl und alarmiert NUR bei FAIL.
# Rein lesend gegenueber Geld- und Truth-Pfad; scharf via
# `systemctl enable --now kai-ln-reconcile-verdict.timer`.

# NOTE: kai-funding-refresh.timer (V5 microstructure evidence) is intentionally
# ABSENT from ENABLE_ON_INSTALL below — installed (daemon-reload aware) but
# disabled by default. Even when enabled it only warms read-only disk caches
# (artifacts/{funding,oi,ls}_cache.json) that the Bayes evidence providers
# consume ONLY when their per-source *_EVIDENCE_ENABLED flags are set.

# NOTE: kai-real-analysis-paper-feed.timer is intentionally ABSENT from
# ENABLE_ON_INSTALL below — it is installed (daemon-reload aware) but stays
# disabled until the operator deliberately enables it. Even when enabled it is a
# no-op until the three-arm REAL_ANALYSIS_PAPER override is armed (Goal 2026-06-10).

# NOTE: kai-shadow-real-feed.timer (Issue #175 wiring) is intentionally ABSENT
# from ENABLE_ON_INSTALL below — installed but disabled by default. Even when
# enabled it is a cheap no-op until EXECUTION_SHADOW_REAL_GENERATOR=true; the
# armed tick runs SHADOW mode only (no order/position/fill, entry_mode untouched).
# NOTE: kai-audit-rotate.timer (S5 audit-stream rotation) is intentionally
# ABSENT from ENABLE_ON_INSTALL — installed but disabled by default. Even when
# enabled it only ARCHIVES allowlisted oversized streams (tail-preserving,
# nothing deleted; paper_execution_audit hard-excluded as engine replay-SSOT).
# NOTE: kai-operator-digest.timer (S6) is intentionally ABSENT from
# ENABLE_ON_INSTALL — installed but disabled by default. Read-only digest;
# sends one Telegram message per day once the operator enables it.
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
    "kai-auto-annotate-blocked.timer"
    "kai-recalc-cycle.timer"
)

# NOTE 2026-08-02: kai-ln-scb-monitor.timer ist ABSICHTLICH nicht in
# ENABLE_ON_INSTALL — installiert, aber erst nach Konfiguration zu aktivieren.
# Auf dem Pi ist APP_LN_SCB_PATH ungesetzt und es existiert dort GAR KEINE
# channel.backup: der Off-node-Pull laeuft per Operator-Entscheid "Weg A"
# (2026-07-14) auf der WORKSTATION nach KAI-mirror/lightning-scb/, ausdruecklich
# um keinen weiteren SSH-Trust zum LN-Node (.51) aufzumachen. Ein hier
# automatisch scharf geschalteter Stundentimer waere gegen einen Pfad gelaufen,
# den niemand befuellt. Aktivieren erst, wenn APP_LN_SCB_PATH auf eine Kopie
# zeigt, die auch tatsaechlich aufgefrischt wird:
#   systemctl enable --now kai-ln-scb-monitor.timer

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
BROKER_ONLY=0
FORCE_UNITS=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --force) FORCE=1 ;;
        --no-enable) NO_ENABLE=1 ;;
        --reactivate) REACTIVATE_ONLY=1 ;;
        --broker-only) BROKER_ONLY=1 ;;
        --force-units) FORCE_UNITS=1 ;;
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

# Freeze-Preflight: Marker EINMAL auswerten (vor jeder Schleife → kein partieller
# Reactivate, bevor ein Fehler erkannt wird). Fail-CLOSED: invalider Marker = HOLD.
# Rückgabe: 0 = weiter (evtl. mit Skips), 1 = HOLD (Aufrufer muss abbrechen).
_paper_freeze_preflight() {
    paper_writer_freeze_state
    PAPER_WRITER_FREEZE_STATE=$?
    if (( PAPER_WRITER_FREEZE_STATE == 20 )); then
        echo "ERROR: PAPER_WRITER_FREEZE_MARKER_INVALID — Marker vorhanden, aber unlesbar/ungültiges Schema." >&2
        echo "       Fail-closed: KEINE Unit wird reaktiviert/enabled. Marker prüfen oder entfernen." >&2
        return 1
    fi
    if (( PAPER_WRITER_FREEZE_STATE == 10 )); then
        echo "PAPER_WRITER_FREEZE_ACTIVE — geschützte Paper-Writer werden übersprungen (kein start/restart/enable/reset-failed/unmask):"
        printf '    %s\n' "${PAPER_WRITER_PROTECTED_UNITS[@]}"
    elif (( PAPER_WRITER_FREEZE_STATE == 0 )) && paper_writer_freeze_marker_present; then
        # Valider Marker mit frozen=false → veraltet; Betrieb normal, aber Hinweis.
        echo "WARN: PAPER_WRITER_FREEZE_MARKER_STALE — Marker vorhanden mit frozen=false; sollte entfernt werden." >&2
    fi
    return 0
}

# 0 wenn die Unit bei aktivem Freeze übersprungen werden muss.
_paper_freeze_skip() {
    (( PAPER_WRITER_FREEZE_STATE == 10 )) && paper_writer_is_protected "$1"
}

# Enable-Schleife als eigene Funktion (source-bar für Tests). Guard identisch
# zum Reactivate-Pfad: invalider Marker = HOLD (return 3, keine Mutation),
# frozen = geschützte Writer überspringen. Rückgabe 0 = ok, 3 = HOLD.
# Release-gebundene Units zeigen mit WorkingDirectory, .venv, --repo und dem
# Kommando nach `--` auf `/home/kai/current`. Legt niemand dieses Ziel an, starten
# sie in einen nicht existierenden Pfad und scheitern sofort — das ist kein
# Provenance-Befund, sondern ein toter Dienst, und zwar bevor irgendeine Sonde
# etwas zu bewerten haette.
#
# Die Unit-Menge kommt aus derselben selbstpflegenden Quelle wie
# `expected_attesting_units`: wer `runtime-exec` im ExecStart fuehrt, ist
# release-gebunden. Eine handgefuehrte Liste waere die naechste Wachliste, die
# von ihrer Quelle abweicht.
#
# Verbindliche Reihenfolge beim Deploy:
#   pi_make_release.sh -> pi_activate_release.sh -> DIESER Installer
#   -> daemon-reload -> Restart -> Runtime-Provenance
release_bound_units() {
    grep -l "runtime-exec" "$UNIT_SRC"/*.service 2>/dev/null | xargs -r -n1 basename
}

release_target_of() {
    # Der `--repo`-Wert der Unit, also das Ziel, das existieren MUSS.
    awk '{for(i=1;i<=NF;i++) if($i=="--repo") {print $(i+1); exit}}' "$UNIT_SRC/$1"
}

assert_release_ready() {
    local unit target problems=0
    for unit in $(release_bound_units); do
        target="$(release_target_of "$unit")"
        [ -n "$target" ] || continue
        if [ ! -d "$target" ]; then
            echo "  FEHLT  $unit -> $target existiert nicht" >&2
            problems=$((problems + 1))
        elif [ ! -f "$target/release.json" ]; then
            echo "  FEHLT  $unit -> $target ohne release.json" >&2
            problems=$((problems + 1))
        fi
    done
    if [ "$problems" -gt 0 ]; then
        echo "" >&2
        echo "ABBRUCH: $problems release-gebundene Unit(s) ohne gueltiges Release." >&2
        echo "Erst  bash scripts/pi_make_release.sh  und  bash scripts/pi_activate_release.sh," >&2
        echo "dann diesen Installer. Ein Start in einen leeren Pfad erzeugt tote Dienste." >&2
        return 1
    fi
    return 0
}

enable_on_install_units() {
    echo ""
    echo "Enabling units so they start at boot…"
    if ! _paper_freeze_preflight; then
        return 3
    fi
    if ! assert_release_ready; then
        return 4
    fi
    local unit
    for unit in "${ENABLE_ON_INSTALL[@]}"; do
        if _paper_freeze_skip "$unit"; then
            echo "  SKIP  $unit — PAPER_WRITER_FREEZE_ACTIVE (eingefrorener Writer; nicht enabled)"
            continue
        fi
        run systemctl enable --now "$unit"
    done
    return 0
}

reactivate_critical() {
    local failed=0
    echo ""
    echo "=== Reactivate-Hook: verifying critical services ==="
    if ! _paper_freeze_preflight; then
        return 3
    fi
    for unit in "${CRITICAL_REACTIVATE[@]}"; do
        if _paper_freeze_skip "$unit"; then
            echo "  SKIP  $unit — PAPER_WRITER_FREEZE_ACTIVE (eingefrorener Writer; nicht angefasst)"
            continue
        fi
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

# Privilegien-Broker: root:root 0755. Bewusst NICHT `ubuntu`-schreibbar —
# der Inhalt ist das Privileg, nicht der Dateiname. Ein von `ubuntu`
# beschreibbares NOPASSWD-Ziel waere exakt so viel wert wie NOPASSWD:ALL.
#
# Eigene Funktion, damit `--broker-only` sie aufrufen kann, OHNE eine einzige
# Unit-Datei anzufassen. Genau dieses Buendel war der Befund vom 2026-08-21:
# wer den Broker installieren wollte, wendete nebenbei 24 divergente Units an.
install_broker() {
    if [[ ! -f "$BROKER_SRC" ]]; then
        echo "FATAL: $BROKER_SRC fehlt — die NOPASSWD-Policy zeigt dann ins Leere." >&2
        exit 1
    fi
    echo ""
    echo "Installing privilege broker (Ziel der NOPASSWD-Policy)…"
    run command install -m 0755 -o root -g root "$BROKER_SRC" "$BROKER_DST"
    # Nachbedingungen BEWEISEN, nicht annehmen: ohne diese Pruefung faellt ein
    # stiller Fehlschlag erst auf, wenn die Recovery gebraucht wird.
    if [[ "${DRY_RUN:-0}" != "1" ]]; then
        local actual
        actual="$(stat -c '%U:%G:%a' "$BROKER_DST" 2>/dev/null || echo 'MISSING')"
        if [[ "$actual" != "root:root:755" ]]; then
            echo "FATAL: $BROKER_DST hat '$actual', erwartet 'root:root:755'." >&2
            exit 1
        fi
        if ! cmp -s "$BROKER_SRC" "$BROKER_DST"; then
            echo "FATAL: $BROKER_DST weicht vom Repo-Artefakt ab." >&2
            exit 1
        fi
        echo "  broker ok: root:root 0755, inhaltsgleich mit dem Repo"
    fi
}

# Nur den Broker. Keine Unit, kein daemon-reload, kein enable — der Eingriff
# mit der kleinsten Angriffsflaeche, den der P0-Weg braucht.
broker_only() {
    require_root
    install_broker
    echo ""
    echo "--broker-only: KEINE Unit-Datei angefasst."
    echo "Unit-Drift gehoert in den Operator-Pfad:"
    echo "  bash scripts/pi_apply_systemd_units.sh --dry-run"
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
        echo "         Run on the laptop: bash scripts/pi_deploy_web.sh ubuntu@192.168.178.23" >&2
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

    # AUDIT-A4: the unit files hard-code $EXPECTED_ROOT (/home/kai/...), which on
    # the Pi is a symlink to /home/ubuntu. If that path does not resolve, every
    # unit boots into a non-existent WorkingDirectory/ReadWritePaths and fails
    # (the 209/STDOUT cutover bug, 2026-05-07). Fail-fast at install time rather
    # than discovering it after a silent boot failure.
    if (( DRY_RUN == 0 )) && [[ ! -d "$EXPECTED_ROOT" ]]; then
        echo "ERROR: unit path $EXPECTED_ROOT does not resolve on this host." >&2
        echo "       The systemd units require it (directly or via the" >&2
        echo "       /home/kai -> /home/ubuntu symlink). Fix before installing:" >&2
        echo "         sudo ln -s /home/ubuntu /home/kai" >&2
        echo "       or re-checkout at $EXPECTED_ROOT." >&2
        exit 1
    fi

    # Massenkopie NUR bei Provisionierung. Liegen im Ziel abweichende Units,
    # waere das eine Live-Aenderung ohne Sicherung, Freeze-Guard, Beweis und
    # Rueckweg — und wuerde `pi_apply_systemd_units.sh` stillschweigend umgehen.
    if (( FORCE_UNITS == 0 )) && ! pi_install_units_allowed "$UNIT_SRC" "$UNIT_DST"; then
        pi_install_units_refusal "$(pi_install_units_drift "$UNIT_SRC" "$UNIT_DST" | wc -l)"
        exit 1
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

    if [[ -f "$HELPER_SRC" ]]; then
        echo ""
        echo "Installing standby helper (ExecStart-Ziel der Cold-Standby-Units)…"
        run command install -m 0755 "$HELPER_SRC" "$HELPER_DST"
    else
        echo "WARNING: $HELPER_SRC fehlt — kai-standby-* wuerden ins Leere zeigen." >&2
    fi

    install_broker

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

    if ! enable_on_install_units; then
        echo "Abbruch vor dem Enable: Freeze-Marker ungültig (fail-closed)." >&2
        exit 3
    fi

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

# Nur ausführen, wenn direkt gestartet — beim Sourcen (Tests) NICHT dispatchen.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if (( BROKER_ONLY == 1 )); then
        broker_only
        exit $?
    elif (( REACTIVATE_ONLY == 1 )); then
        reactivate_only
        exit $?
    elif (( UNINSTALL == 1 )); then
        uninstall
    else
        install
    fi
fi
