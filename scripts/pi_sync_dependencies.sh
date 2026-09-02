#!/usr/bin/env bash
# scripts/pi_sync_dependencies.sh — Abhaengigkeiten eines Checkouts synchronisieren
# und den Beweis dafuer schreiben. Vom Operator oder vom Deploy aufrufbar.
#
# WARUM ES DIESES SKRIPT BRAUCHT (Befund 2026-09-01):
#
#   * `source .venv/bin/activate && pip install ...` griff auf `/usr/bin/pip`,
#     weil der venv KEIN `pip`-Skript hat — und scheiterte an PEP 668. Richtig
#     ist immer `<venv>/bin/python3 -m pip`.
#   * `pip install ... | tail -5; echo $?` liest den Exit von `tail`, nicht von
#     pip. Der Lauf sah erfolgreich aus, obwohl er abgebrochen war.
#   * Nach dem Update liefen Dienste weiter, die ihre Bibliotheken seit Tagen im
#     Speicher hielten. Weder `active` noch `/health=200` sehen das.
#
# Der Marker wird deshalb NUR geschrieben, wenn Installation UND `pip check`
# tatsaechlich getragen haben. Ein Marker ueber einen gescheiterten Lauf waere
# schlimmer als keiner — er saehe beim naechsten Deploy wie ein Beweis aus.
#
# Usage:
#   bash scripts/pi_sync_dependencies.sh [--repo <pfad>] [--venv <pfad>] [--dry-run]
#
# Exit: 0 = synchronisiert und Marker geschrieben · 1 = gescheitert (kein Marker)
set -uo pipefail

REPO="."
VENV=""
DRY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --venv) VENV="$2"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        *) echo "unbekanntes Argument: $1" >&2; exit 1 ;;
    esac
done

REPO="$(cd "$REPO" 2>/dev/null && pwd)" || { echo "kein Checkout: $REPO" >&2; exit 1; }
[ -n "$VENV" ] || VENV="$REPO/.venv"
PY="$VENV/bin/python3"
LOCK="$REPO/requirements.lock"

[ -x "$PY" ] || { echo "kein Interpreter: $PY" >&2; exit 1; }
[ -f "$LOCK" ] || { echo "kein requirements.lock: $LOCK" >&2; exit 1; }

REPO_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null)" || {
    echo "kein Git-Checkout: $REPO" >&2; exit 1; }
LOCK_SHA="$(sha256sum "$LOCK" | cut -d' ' -f1)"

echo "repo      : $REPO ($(echo "$REPO_SHA" | cut -c1-8))"
echo "python    : $PY"
echo "lock      : $LOCK  sha256=$(echo "$LOCK_SHA" | cut -c1-16)"

if [ "$DRY" = "1" ]; then
    echo "--dry-run: nichts installiert, kein Marker geschrieben."
    exit 0
fi

# Exit-Codes NICHT durch eine Pipe verlieren — genau daran ist der Lauf am
# 01.09. scheinbar erfolgreich gewesen.
echo "== install =="
if ! "$PY" -m pip install -r "$LOCK"; then
    echo "pip install FEHLGESCHLAGEN — kein Marker geschrieben." >&2
    exit 1
fi

echo "== pip check =="
if ! "$PY" -m pip check; then
    echo "pip check FEHLGESCHLAGEN — kein Marker geschrieben." >&2
    exit 1
fi

MARKER_DIR="$REPO/artifacts/runtime"
MARKER="$MARKER_DIR/dependency_marker.json"
mkdir -p "$MARKER_DIR"
NOW="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
cat > "$MARKER" <<EOF
{
  "schema": "dependency_marker/v1",
  "repo_sha": "$REPO_SHA",
  "requirements_lock_sha256": "$LOCK_SHA",
  "python_executable": "$PY",
  "installed_at_utc": "$NOW"
}
EOF

# Deploy-Marker: WANN wurde auf WELCHEN Stand deployt. Ohne ihn laesst sich
# spaeter nicht sagen, ob ein Prozess vor oder nach dem Deploy gestartet ist —
# genau die Luecke, durch die am 2026-09-01 ein Prozess auf dc276bc3 lief,
# waehrend der Checkout auf 9293c423 stand.
DEPLOY_MARKER="$MARKER_DIR/deployment_marker.json"
cat > "$DEPLOY_MARKER" <<EOF
{
  "schema": "deployment_marker/v1",
  "repo_sha": "$REPO_SHA",
  "requirements_lock_sha256": "$LOCK_SHA",
  "deployed_at_utc": "$NOW"
}
EOF

echo "== Marker geschrieben =="
cat "$MARKER"
cat "$DEPLOY_MARKER"
echo
echo "HINWEIS: laufende Dienste halten ihre Bibliotheken im Speicher. Ohne"
echo "Neustart aendert diese Installation an ihnen nichts — pruefe danach"
echo "  kai trading runtime-provenance"
echo "Jeder langlebige Dienst muss NACH diesem Zeitpunkt neu gestartet werden;"
echo "sonst meldet der Health-Check RUNTIME_STALE_NO_RESTART statt gruen."
exit 0
