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
#
# Exit: 0 = deployt und verifiziert · 3 = Vorbedingung verletzt (nichts
# angefasst) · 1 = Bau/Aktivierung/Verifikation gescheitert (siehe Ausgabe).
set -uo pipefail

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pi_release_guard.sh
. "$_here/lib/pi_release_guard.sh"

SHA=""; EXPECT=""; DRY=0
REPO="${KAI_PI_REPO:-/home/ubuntu/ai_analyst_trading_bot}"
RELEASES="${KAI_PI_RELEASES:-/home/ubuntu/releases}"
CURRENT="${KAI_PI_CURRENT:-/home/ubuntu/current}"
BROKER="${PI_DEPLOY_BROKER:-sudo -n /usr/local/sbin/kai-service-control}"
HEALTH_URL="${KAI_PI_HEALTH_URL:-http://127.0.0.1:8000/health}"

while [ $# -gt 0 ]; do
    case "$1" in
        --sha) SHA="${2:-}"; shift 2 ;;
        --expect-current) EXPECT="${2:-}"; shift 2 ;;
        --repo) REPO="${2:-}"; shift 2 ;;
        --releases) RELEASES="${2:-}"; shift 2 ;;
        --current) CURRENT="${2:-}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
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
sleep 20

echo "== Verifikation"
rc=0
echo "current=$(readlink -f "$CURRENT")"
[ "$(readlink -f "$CURRENT")" = "$RELEASES/$SHA" ] || { echo "FAIL current zeigt nicht auf $SHA"; rc=1; }
health="$(curl -s --max-time 8 "$HEALTH_URL" || true)"
echo "health=$health"
printf '%s' "$health" | grep -q "\"runtime_commit\":\"$SHA\"" || { echo "FAIL /health meldet nicht runtime_commit=$SHA"; rc=1; }
printf '%s' "$health" | grep -q '"drift_commits":0' || { echo "FAIL drift_commits != 0"; rc=1; }
for u in $units; do
    pid="$(systemctl show -p MainPID --value "$u")"
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || echo '?')"
    state="$(systemctl is-active "$u")"
    echo "$u active=$state cwd=$cwd"
    { [ "$state" = "active" ] && [ "$cwd" = "$RELEASES/$SHA" ]; } || { echo "FAIL $u nicht aktiv im neuen Release"; rc=1; }
done
failed="$(systemctl --failed --no-legend | wc -l)"
echo "failed_units=$failed"
[ "$failed" -eq 0 ] || rc=1
[ "$rc" -eq 0 ] && echo "DEPLOY_VERIFIED $SHA" || echo "DEPLOY_NOT_VERIFIED $SHA"
exit "$rc"
