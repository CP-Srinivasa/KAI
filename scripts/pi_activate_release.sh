#!/usr/bin/env bash
# scripts/pi_activate_release.sh — `current` atomar umschalten, Deploy-Marker
# schreiben, alte Releases aufraeumen.
#
# REIHENFOLGE, und sie ist nicht beliebig:
#
#   Release fertig -> Units installieren -> daemon-reload
#   -> ATOMARER current-Switch -> Deploy-Marker -> Restarts -> Marker pruefen
#
# Der Deploy-Marker darf NICHT sagen "SHA X ist aktiv", waehrend `current` noch
# auf SHA Y zeigt. Deshalb: erst schalten, dann schreiben. Und niemals zuerst
# restarten und danach die Units austauschen — der erste Start nach dem Deploy
# muss bereits unter dem neuen Attestierungsvertrag laufen.
#
# Usage:
#   bash scripts/pi_activate_release.sh --release <pfad> [--current <link>]
#                                       [--state <dir>] [--keep 3]
#
# Exit: 0 = aktiv und markiert · 1 = gescheitert (nichts umgeschaltet)
set -uo pipefail

RELEASE=""
CURRENT=""
STATE=""
KEEP=3
while [ $# -gt 0 ]; do
    case "$1" in
        --release) RELEASE="$2"; shift 2 ;;
        --current) CURRENT="$2"; shift 2 ;;
        --state) STATE="$2"; shift 2 ;;
        --keep) KEEP="$2"; shift 2 ;;
        *) echo "unbekanntes Argument: $1" >&2; exit 1 ;;
    esac
done

[ -n "$RELEASE" ] || { echo "--release fehlt" >&2; exit 1; }
RELEASE="$(cd "$RELEASE" 2>/dev/null && pwd)" || { echo "kein Release: $RELEASE" >&2; exit 1; }
RELEASES_DIR="$(dirname "$RELEASE")"
[ -n "$CURRENT" ] || CURRENT="$(dirname "$RELEASES_DIR")/current"
[ -n "$STATE" ] || STATE="$(dirname "$RELEASES_DIR")/ai_analyst_trading_bot"
PY="$RELEASE/.venv/bin/python3"

echo "== Release gegen seinen eigenen Anspruch pruefen ==" >&2
if ! "$PY" -c "
import sys
sys.path.insert(0, '$RELEASE')
from app.observability.release_identity import verify_release
from pathlib import Path
probleme = verify_release(Path('$RELEASE'))
if probleme:
    print(', '.join(probleme), file=sys.stderr)
    sys.exit(1)
"; then
    echo "Release traegt seinen Anspruch nicht — NICHT aktiviert" >&2
    exit 1
fi

echo "== current atomar umschalten ==" >&2
# ln -sfn ist NICHT atomar (es entfernt und legt neu an). Ein Symlink daneben
# plus `mv -T` ersetzt in EINEM rename(2) — dazwischen gibt es keinen Moment, in
# dem `current` ins Leere zeigt.
TMPLINK="$CURRENT.new.$$"
ln -sfn "$RELEASE" "$TMPLINK" || { echo "Symlink-Anlage gescheitert" >&2; exit 1; }
mv -T "$TMPLINK" "$CURRENT" || { echo "Umschalten gescheitert" >&2; rm -f "$TMPLINK"; exit 1; }
RESOLVED="$(readlink -f "$CURRENT")"
[ "$RESOLVED" = "$RELEASE" ] || {
    echo "current zeigt auf $RESOLVED statt $RELEASE" >&2; exit 1; }

echo "== Deploy-Marker (NACH dem Switch) ==" >&2
mkdir -p "$STATE/artifacts/runtime"
"$PY" - "$RELEASE" "$STATE" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

release, state = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(release))
from app.observability.release_identity import read_release_manifest  # noqa: E402

m = read_release_manifest(release)
if m is None:
    raise SystemExit("release.json unlesbar")
target = state / "artifacts" / "runtime" / "deployment_marker.json"
tmp = target.with_suffix(".json.new")
tmp.write_text(
    json.dumps(
        {
            "schema": "deployment_marker/v1",
            "repo_sha": m.repo_sha,
            "release_path": m.release_path,
            "release_tree_sha256": m.release_tree_sha256,
            "requirements_lock_sha256": m.requirements_lock_sha256,
            "deployed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
tmp.replace(target)  # atomar
print(f"DEPLOY_MARKER={target}")
PY
[ $? -eq 0 ] || { echo "Deploy-Marker gescheitert" >&2; exit 1; }

echo "== Aufraeumen ==" >&2
# NIE stumpf "aelteste nach mtime". Geloescht wird nur, was nachweislich
# niemand mehr braucht: nicht current, von keinem lebenden Prozessmarker
# referenziert, und innerhalb der Aufbewahrung.
LIVE="$(grep -ho '"release_path": *"[^"]*"' "$STATE"/artifacts/runtime/processes/*.json 2>/dev/null \
        | sed 's/.*: *"//;s/"$//' | sort -u)"
KEPT=0
for d in $(ls -1dt "$RELEASES_DIR"/*/ 2>/dev/null); do
    d="${d%/}"
    case "$d" in *"/.staging-"*) continue ;; esac
    KEPT=$((KEPT+1))
    [ "$d" = "$RESOLVED" ] && continue
    if [ "$KEPT" -le "$KEEP" ]; then continue; fi
    if printf '%s\n' "$LIVE" | grep -qxF "$d"; then
        echo "  behalten (lebender Prozess): $d" >&2
        continue
    fi
    chmod -R u+w "$d" 2>/dev/null
    rm -rf "$d" && echo "  entfernt: $d" >&2
done

echo "ACTIVE_RELEASE=$RESOLVED" >&2
exit 0
