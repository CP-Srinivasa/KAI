#!/usr/bin/env bash
# scripts/pi_release_deploy.sh — der kanonische Release-Deploy AUF DER PI:
# Vorbedingungen -> ff-Checkout -> Release bauen -> aktivieren -> ALLE
# release-gebundenen Units neu starten -> verifizieren.
#
# WARUM DIESE DATEI IM REPO LIEGT: die Deploys vom 11. bis 14.09.2026 liefen
# ueber ein Skript in /tmp, das nur vier Units neu startete. kai-liquidation-
# stream und kai-entry-watch lasen ihre Unit-Dateien aus einem drei Deploys
# alten Release, ohne dass /health etwas davon zeigte -- /health kennt nur
# kai-server. Die Liste der Units steht hier NICHT von Hand: sie kommt aus
# `pi_release_bound_units` (scripts/lib/pi_release_guard.sh), derselben Quelle,
# aus der Unit-Sync und Tests ihre Sollmenge ziehen. Eine handgefuehrte Liste
# waere die naechste Wachliste, die von ihrer Quelle abweicht.
#
# Aufruf:
#   bash scripts/pi_release_deploy.sh --sha <voller-sha> [--expect-current <voller-sha>]
#        [--repo <checkout>] [--releases <dir>] [--current <link>] [--dry-run]
#        [--allow-inflight]   (nur mit Operator-Freigabe: Deploy trotz Zahlung unterwegs)
#
# Exit: 0 = deployt und verifiziert · 3 = Vorbedingung verletzt (nichts
# angefasst) · 1 = Bau/Aktivierung/Verifikation gescheitert (siehe Ausgabe).
set -uo pipefail

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pi_release_guard.sh
. "$_here/lib/pi_release_guard.sh"
# shellcheck source=scripts/lib/pi_wait_until.sh
. "$_here/lib/pi_wait_until.sh"

SHA=""; EXPECT=""; DRY=0
REPO="${KAI_PI_REPO:-/home/ubuntu/ai_analyst_trading_bot}"
RELEASES="${KAI_PI_RELEASES:-/home/ubuntu/releases}"
CURRENT="${KAI_PI_CURRENT:-/home/ubuntu/current}"
BROKER="${PI_DEPLOY_BROKER:-sudo -n /usr/local/sbin/kai-service-control}"
HEALTH_URL="${KAI_PI_HEALTH_URL:-http://127.0.0.1:8000/health}"
# Frist fuer die Verifikation nach den Restarts. 180 s tragen einen Kaltstart
# von kai-server samt entry-watch-Zyklus (Unit startet alle ~69 s neu).
VERIFY_TIMEOUT_S="${KAI_PI_VERIFY_TIMEOUT_S:-180}"
VERIFY_INTERVAL_S="${KAI_PI_VERIFY_INTERVAL_S:-5}"

while [ $# -gt 0 ]; do
    case "$1" in
        --sha) SHA="${2:-}"; shift 2 ;;
        --expect-current) EXPECT="${2:-}"; shift 2 ;;
        --repo) REPO="${2:-}"; shift 2 ;;
        --releases) RELEASES="${2:-}"; shift 2 ;;
        --current) CURRENT="${2:-}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        --allow-inflight) ALLOW_INFLIGHT=1; shift ;;
        *) echo "unbekanntes Argument: $1" >&2; exit 3 ;;
    esac
done

case "$SHA" in
    *[!0-9a-f]*|"") echo "ABBRUCH: --sha muss ein voller 40-stelliger SHA sein (bekam '${SHA}')" >&2; exit 3 ;;
esac
[ "${#SHA}" -eq 40 ] || { echo "ABBRUCH: --sha muss 40 Zeichen haben, bekam ${#SHA} (Kurz-SHA scheitert erst NACH dem ff-Pull)" >&2; exit 3; }

# Die Units des ZIELS, nicht des laufenden Releases: das neue Release kann eine
# Unit dazubekommen haben.
units_of() { pi_release_bound_units "$1/deploy/systemd" | sed 's/\.service$//'; }

echo "== Plan"
echo "sha=$SHA repo=$REPO releases=$RELEASES current=$CURRENT"
if [ -d "$REPO/deploy/systemd" ]; then
    echo "release_bound_units(checkout)=$(units_of "$REPO" | tr '\n' ' ')"
fi
if [ "$DRY" -eq 1 ]; then echo "DRY_RUN: nichts ausgefuehrt"; exit 0; fi

echo "== Vorbedingungen"
if pgrep -af "pi_make_release|pi_activate_release" | grep -v pgrep >/dev/null; then
    echo "ABBRUCH: paralleler Deploy laeuft"; exit 3
fi
aktiv="$(readlink -f "$CURRENT")"
if [ -n "$EXPECT" ] && [ "$aktiv" != "$RELEASES/$EXPECT" ]; then
    echo "ABBRUCH: current=$aktiv, erwartet $RELEASES/$EXPECT"; exit 3
fi
if [ "$(systemctl --failed --no-legend | wc -l)" -ne 0 ]; then
    echo "ABBRUCH: failed units vor dem Deploy"; systemctl --failed --no-legend; exit 3
fi
if [ "$aktiv" = "$RELEASES/$SHA" ]; then
    echo "ABBRUCH: $SHA ist bereits aktiv"; exit 3
fi
# Kein Geld unterwegs (Lueckenregister 26.09.): die Restarts treffen kai-server;
# ein Neustart zwischen Freigabe und Node-Antwort muesste der Reconciler erst
# hinterher klaeren. Rein lesend. Fehlt die Pruefung im Checkout (erster Deploy,
# der sie mitbringt), wird das laut gesagt statt still uebersprungen.
if [ "${ALLOW_INFLIGHT:-0}" -eq 1 ]; then
    echo "WARNUNG: --allow-inflight gesetzt, Zahlungs-Drain-Check uebersprungen"
elif [ -f "$REPO/scripts/payment_drain_check.py" ] && [ -x "$REPO/.venv/bin/python" ]; then
    ( cd "$REPO" && ./.venv/bin/python -m scripts.payment_drain_check \
        --journal "$REPO/artifacts/payments/payment_journal.jsonl" ) \
        || { echo "ABBRUCH: Zahlung unterwegs oder Journal unlesbar (--allow-inflight nur mit Operator-Freigabe)"; exit 3; }
else
    echo "WARNUNG: Zahlungs-Drain-Check fehlt im Checkout (Bootstrap) -- ab dem naechsten Deploy aktiv"
fi

echo "== Checkout $SHA (ff-only)"
cd "$REPO" || exit 1
git fetch -q origin || { echo "ABBRUCH: fetch"; exit 1; }
git merge --ff-only "$SHA" || { echo "ABBRUCH: kein ff auf $SHA"; exit 1; }
[ "$(git rev-parse HEAD)" = "$SHA" ] || { echo "ABBRUCH: HEAD != $SHA"; exit 1; }

echo "== Release bauen"
bash scripts/pi_make_release.sh --repo "$REPO" --releases "$RELEASES" --state "$REPO" || exit 1

echo "== Aktivieren"
bash scripts/pi_activate_release.sh --release "$RELEASES/$SHA" --current "$CURRENT" --state "$REPO" || exit 1

echo "== Restarts (alle release-gebundenen Units des neuen Release)"
units="$(units_of "$RELEASES/$SHA")"
[ -n "$units" ] || { echo "ABBRUCH: keine release-gebundene Unit im Release gefunden"; exit 1; }
for u in $units; do
    $BROKER restart "$u.service" >/dev/null 2>&1 || echo "RESTART_FAILED $u"
done
# Verifikation: EIN Satz Kriterien, zweimal benutzt -- still im Warten, laut im
# Bericht. Vorher: feste 20 s Pause und genau ein Versuch; beim Deploy ff93050c
# (17.09.) war /health da noch leer -> DEPLOY_NOT_VERIFIED fuer einen gesunden
# Deploy. Jetzt wird gewartet, bis alles besteht, hoechstens VERIFY_TIMEOUT_S.
verify_release() {  # $1 = quiet|loud ; Exit 0 = alles erfuellt
    local mode="$1" ok=0 health pid cwd state failed
    say() { [ "$mode" = loud ] && echo "$@"; return 0; }
    say "current=$(readlink -f "$CURRENT")"
    [ "$(readlink -f "$CURRENT")" = "$RELEASES/$SHA" ] || { say "FAIL current zeigt nicht auf $SHA"; ok=1; }
    health="$(curl -s --max-time 8 "$HEALTH_URL" || true)"
    say "health=$health"
    printf '%s' "$health" | grep -q "\"runtime_commit\":\"$SHA\"" || { say "FAIL /health meldet nicht runtime_commit=$SHA"; ok=1; }
    printf '%s' "$health" | grep -q '"drift_commits":0' || { say "FAIL drift_commits != 0"; ok=1; }
    for u in $units; do
        pid="$(systemctl show -p MainPID --value "$u")"
        cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || echo '?')"
        state="$(systemctl is-active "$u")"
        say "$u active=$state cwd=$cwd"
        { [ "$state" = "active" ] && [ "$cwd" = "$RELEASES/$SHA" ]; } || { say "FAIL $u nicht aktiv im neuen Release"; ok=1; }
    done
    failed="$(systemctl --failed --no-legend | wc -l)"
    say "failed_units=$failed"
    [ "$failed" -eq 0 ] || ok=1
    return "$ok"
}

started=$(date +%s)
if pi_wait_until "$VERIFY_TIMEOUT_S" "$VERIFY_INTERVAL_S" verify_release quiet; then
    echo "verifiziert nach $(( $(date +%s) - started )) s"
else
    echo "Frist ${VERIFY_TIMEOUT_S} s abgelaufen -- Stand zum Fristende:"
fi

echo "== Verifikation"
rc=0
verify_release loud || rc=1
[ "$rc" -eq 0 ] && echo "DEPLOY_VERIFIED $SHA" || echo "DEPLOY_NOT_VERIFIED $SHA"
exit "$rc"
