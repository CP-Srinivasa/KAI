#!/usr/bin/env bash
# scripts/pi_deploy_step.sh — der Deploy-Schritt, der AUF DER PI laeuft.
#
# WARUM DIESE DATEI IM REPO LIEGT: bis 2026-08-20 stand die gesamte Logik als
# ssh-Heredoc in `~/KAI-mirror/scripts/kai_deploy.sh` — einer Datei ausserhalb
# der Versionskontrolle und ausserhalb CI. Dort konnte
#
#     pi_unit_sync_apply || echo "unit-sync: rc=$? ..."
#
# unbemerkt stehen bleiben: der Sync scheiterte an `sudo: a password is
# required`, `|| echo` verwandelte den Fehlschlag in eine Notiz, der /health-Smoke
# fand danach 200 (der Server war nie angefasst worden) und der Deploy meldete
# Gruen. 24 Unit-Dateien blieben divergent. Kein Test konnte das je finden, weil
# kein Test die Datei sehen konnte.
#
# Aufrufkonvention (von kai_deploy.sh):
#     bash scripts/pi_deploy_step.sh --base <sha-vor-dem-merge> --branch <name> \
#          [--restart a,b] [--check f.py] [--no-unit-sync]
#
# Exit-Codes sind kanonisch (siehe scripts/lib/pi_deploy_verdict.sh):
#     0 = DEPLOY_SUCCESS · 10 = DEPLOY_HOLD · alles andere = DEPLOY_FAILED
set -uo pipefail

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pi_deploy_verdict.sh
. "$_here/lib/pi_deploy_verdict.sh"
# shellcheck source=scripts/lib/pi_unit_sync.sh
. "$_here/lib/pi_unit_sync.sh"
# Der Freeze-Guard darf fehlen (aeltere Checkouts); das ist ein WARN, kein Abbruch.
# shellcheck source=scripts/lib/paper_writer_freeze.sh
. "$_here/lib/paper_writer_freeze.sh" 2>/dev/null || true

base=""
branch="${KAI_MAINLINE_BRANCH:-claude/p7/reentry-ia-codex-cycle}"
restart=""
checks=""
unit_check=1
while [ $# -gt 0 ]; do
    case "$1" in
        --base) base="${2:-}"; shift 2 ;;
        --branch) branch="${2:-}"; shift 2 ;;
        --restart) restart="${2:-}"; shift 2 ;;
        --check) checks="$checks ${2:-}"; shift 2 ;;
        --no-unit-sync) unit_check=0; shift ;;
        *) shift ;;
    esac
done

GIT="${PI_DEPLOY_GIT:-git}"
CURL="${PI_DEPLOY_CURL:-curl}"
HEALTH_URL="${PI_DEPLOY_HEALTH_URL:-http://127.0.0.1:8000/health}"
HEALTH_TRIES="${PI_DEPLOY_HEALTH_TRIES:-10}"
HEALTH_SLEEP="${PI_DEPLOY_HEALTH_SLEEP:-2}"
BROKER="${PI_DEPLOY_BROKER:-sudo -n /usr/local/sbin/kai-service-control}"

extra_reasons=()
server_restarted=0
health_body=""

# ── 1. Code nachziehen ──────────────────────────────────────────────────────
[ -n "$base" ] || base="$($GIT rev-parse HEAD)"
if ! $GIT fetch origin "$branch" 2>&1 | tail -1; then
    echo "FETCH fehlgeschlagen" >&2
    extra_reasons+=("FETCH_FAILED")
fi
if ! $GIT merge --ff-only "origin/$branch" 2>&1 | tail -2; then
    echo "ff-merge fehlgeschlagen — der Checkout ist nicht fast-forwardbar." >&2
    extra_reasons+=("MERGE_FAILED")
fi
after="$($GIT rev-parse HEAD)"
echo "HEAD: $(echo "$base" | cut -c1-8) -> $(echo "$after" | cut -c1-8)"

# ── 2. Unit-Abgleich: NUR MESSEN ────────────────────────────────────────────
# Bewusst read-only. Unit-Dateien sind operator-privilegiert: der Broker
# (`kai-service-control`) startet Dienste, er kopiert keine Dateien nach /etc.
# Ein `sudo -n cp` waere hier nicht autorisiert und wuerde bei JEDEM Deploy mit
# Drift scheitern — genau die Fehlschlag-Zeile, die vorher verschluckt wurde.
# Messen kann der Deploy; anwenden muss der Operator.
drift="0"
if [ "$unit_check" = "1" ]; then
    if unit_out="$(pi_unit_sync_diff "${PI_DEPLOY_UNIT_SRC:-deploy/systemd}" "${PI_DEPLOY_UNIT_DST:-/etc/systemd/system}" 2>&1)"; then
        [ -n "$unit_out" ] && printf '%s\n' "$unit_out" | sed 's/^/unit-drift: /'
        drift="$(printf '%s\n' "$unit_out" | grep -cE '^(DIFF|NEW) ' || true)"
    else
        echo "unit-drift: Abgleich nicht messbar" >&2
        drift="unknown"
    fi
else
    drift="unknown"
fi

# Hat GENAU DIESER Merge die Units veraendert? Trennt "ich bin die Ursache" von
# "der Drift lag schon vorher an" — dieselbe Konsequenz, aber ein anderer Befund.
caused=0
if [ "$base" != "$after" ] && \
   [ -n "$($GIT diff --name-only "$base" "$after" -- deploy/systemd/ 2>/dev/null)" ]; then
    caused=1
fi

# ── 3. Syntaxpruefungen ─────────────────────────────────────────────────────
for f in $checks; do
    case "$f" in
        *.py)
            if python3 -m py_compile "$f"; then
                echo "pycompile_ok:$f"
            else
                echo "pycompile FEHLGESCHLAGEN: $f" >&2
                extra_reasons+=("COMPILE_FAILED")
            fi
            ;;
        *) echo "skip_noncheck:$f" ;;
    esac
done

# ── 4. Restart (nur auf Ansage, nur durch den Broker, nur nach Freeze-Guard) ─
if [ -n "$restart" ]; then
    svc="$(echo "$restart" | tr ',' ' ')"
    guard_ok=1
    if declare -F paper_writer_freeze_guard_restart >/dev/null 2>&1; then
        # shellcheck disable=SC2086
        paper_writer_freeze_guard_restart $svc >/dev/null 2>&1 || guard_ok=0
    else
        echo "WARN: paper_writer_freeze.sh fehlt — Writer-Restart NICHT geguarded." >&2
    fi
    if [ "$guard_ok" = "0" ]; then
        echo "Writer-Freeze aktiv: Restart bewusst NICHT ausgefuehrt." >&2
        extra_reasons+=("WRITER_FREEZE_DEFERRED")
    else
        for s in $svc; do
            if $BROKER restart "${s%.service}.service"; then
                echo "restarted:${s%.service}.service"
                [ "${s%.service}" = "kai-server" ] && server_restarted=1
            else
                echo "Restart FEHLGESCHLAGEN: $s" >&2
                extra_reasons+=("RESTART_FAILED")
            fi
        done
        sleep "${PI_DEPLOY_RESTART_SETTLE:-5}"
    fi
fi

# ── 5. Nachbedingung /health ────────────────────────────────────────────────
# Retry, weil uvicorn nach einem Restart ein paar Sekunden bis zum Port-Bind
# braucht. Ohne ihn meldet der Smoke immer 000 und wird wertlos.
code=000
tries=0
while [ "$tries" -lt "$HEALTH_TRIES" ]; do
    tries=$((tries + 1))
    # Kein `|| echo 000`: ein unerreichbarer Dienst ist ein Fakt, den die
    # Urteilslogik lesen soll — kein Fehlschlag, den man wegschreibt.
    # Body UND Code: seit STAB-02 traegt /health den laufenden Commit — der
    # Deploy liest ihn als Nachbedingung (Restart hat den Code geladen?).
    if raw="$($CURL -s -w '\n%{http_code}' --max-time 5 "$HEALTH_URL")"; then
        code="${raw##*$'\n'}"
        health_body="${raw%$'\n'*}"
    else
        code=000
        health_body=""
    fi
    [ -n "$code" ] || code=000
    [ "$code" = "200" ] && break
    [ "$tries" -lt "$HEALTH_TRIES" ] && sleep "$HEALTH_SLEEP"
done
echo "health:$code (nach ${tries} Versuch(en))"

# ── 6. Urteil ───────────────────────────────────────────────────────────────
# Runtime-Identitaet (STAB-02): nur sinnvoll, wenn /health ueberhaupt antwortete —
# ein toter Server ist schon HEALTH_NOT_200, kein zweiter Grund.
if [ "$code" = "200" ]; then
    runtime_token="$(pi_deploy_runtime_reason "$health_body" "$($GIT rev-parse HEAD)" "$server_restarted")"
    [ -n "$runtime_token" ] && extra_reasons+=("$runtime_token")
fi
mapfile -t reasons < <(pi_deploy_reasons "$code" "$drift" "$caused" ${extra_reasons[@]+"${extra_reasons[@]}"})
verdict="$(pi_deploy_verdict ${reasons[@]+"${reasons[@]}"})"
rc=$?

if [ "${#reasons[@]}" -gt 0 ]; then
    pi_deploy_explain "${reasons[@]}"
fi

# Ein Gate, das kein Mittel nennt, ist nur eine Blockade.
if [ "$drift" != "0" ] && [ "$drift" != "unknown" ]; then
    echo "  Anwenden (Operator, mit Passwort, auf der Pi):"
    printf '%s\n' "$unit_out" | awk '/^(DIFF|NEW) /{print "    sudo cp deploy/systemd/" $2 " /etc/systemd/system/" $2}'
    echo "    sudo systemctl daemon-reload"
    printf '%s\n' "$unit_out" | awk '/^(DIFF|NEW) .*\.timer$/{print "    sudo systemctl restart " $2}'
fi

# Tokens maschinenlesbar neben dem Urteil — der Aufrufer (kai_deploy.sh, CI-Test)
# soll den Grund nicht aus Prosa zurueckparsen muessen.
echo "DEPLOY_REASONS=${reasons[*]-}"
echo "DEPLOY_VERDICT=$verdict"
exit "$rc"
