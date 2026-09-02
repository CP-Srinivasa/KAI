#!/usr/bin/env bash
# scripts/pi_make_release.sh — einen UNVERAENDERLICHEN Release-Baum bauen.
#
# WARUM (Befund 2026-09-02):
#
#   Ein Prozess, der aus einem beweglichen Checkout startet, kann NICHT beweisen,
#   welche Bytes er geladen hat. Python importiert Module erst zur Laufzeit; der
#   Baum darf sich zwischen Attestierung und Import weiterbewegen:
#
#       Checkout OLD -> attestiert OLD -> Checkout wandert auf NEW -> exec
#       -> importiert NEW -> Marker behauptet OLD
#
#   Mehr Logik um den beweglichen Baum herum loest das nicht. Ein Baum, der sich
#   nicht bewegt, schon.
#
# WAS DIESES SKRIPT NICHT TUT: es schaltet `current` NICHT um und schreibt KEINEN
# Deploy-Marker. Das ist `pi_activate_release.sh`. Ein Deploy-Marker, der einen
# Stand behauptet, auf den `current` noch nicht zeigt, waere eine Luege.
#
# Reihenfolge: bauen -> pruefen -> venv aus Lock -> pip check -> release.json ->
# versiegeln. Erst danach Units, daemon-reload, current-Switch, Deploy-Marker,
# Restarts.
#
# Usage:
#   bash scripts/pi_make_release.sh [--repo <checkout>] [--releases <dir>] [--state <dir>]
#
# Exit: 0 = Release gebaut und versiegelt (Pfad auf stdout) · 1 = gescheitert
set -uo pipefail

BUILDER_VERSION="pi_make_release/1"
REPO="."
RELEASES=""
STATE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --releases) RELEASES="$2"; shift 2 ;;
        --state) STATE="$2"; shift 2 ;;
        *) echo "unbekanntes Argument: $1" >&2; exit 1 ;;
    esac
done

REPO="$(cd "$REPO" 2>/dev/null && pwd)" || { echo "kein Checkout: $REPO" >&2; exit 1; }
[ -n "$RELEASES" ] || RELEASES="$(dirname "$REPO")/releases"
[ -n "$STATE" ] || STATE="$REPO"

REPO_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null)" || {
    echo "kein Git-Checkout: $REPO" >&2; exit 1; }
LOCK="$REPO/requirements.lock"
[ -f "$LOCK" ] || { echo "requirements.lock fehlt" >&2; exit 1; }

TARGET="$RELEASES/$REPO_SHA"
STAGE="$RELEASES/.staging-$REPO_SHA.$$"

if [ -d "$TARGET" ]; then
    echo "Release existiert bereits: $TARGET" >&2
    echo "$TARGET"
    exit 0
fi

echo "== 1/6 Code in die Staging-Flaeche ==" >&2
rm -rf "$STAGE"
mkdir -p "$STAGE" || { echo "kann $STAGE nicht anlegen" >&2; exit 1; }
for d in app config deploy scripts; do
    [ -d "$REPO/$d" ] && cp -a "$REPO/$d" "$STAGE/$d"
done
for f in requirements.lock pyproject.toml; do
    [ -f "$REPO/$f" ] && cp -a "$REPO/$f" "$STAGE/$f"
done
# Caches gehoeren nicht in eine Identitaet.
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null

echo "== 2/6 Zustand VERLINKEN, nicht kopieren ==" >&2
# Wanderten .env, logs/, data/ und artifacts/ mit ins Release, verlore jeder
# Deploy den Zustand und jeder Rollback die seither entstandenen Daten. Der Code
# ist unveraenderlich, der Zustand bleibt an einem stabilen Ort.
for s in .env artifacts data logs; do
    ln -sfn "$STATE/$s" "$STAGE/$s"
done

echo "== 3/6 eigener venv aus dem gepinnten Lockfile ==" >&2
# NICHT den vorhandenen venv hineinkopieren: das truege vorhandenen Drift in
# einen angeblich unveraenderlichen Stand. Neu bauen und pruefen.
python3 -m venv "$STAGE/.venv" || { echo "venv-Bau gescheitert" >&2; rm -rf "$STAGE"; exit 1; }
PY="$STAGE/.venv/bin/python3"
"$PY" -m pip install --upgrade pip >/dev/null 2>&1
if ! "$PY" -m pip install -r "$LOCK" >/tmp/kai-release-pip.$$.log 2>&1; then
    echo "pip install gescheitert — siehe /tmp/kai-release-pip.$$.log" >&2
    rm -rf "$STAGE"; exit 1
fi

echo "== 4/6 pip check ==" >&2
if ! "$PY" -m pip check >/dev/null 2>&1; then
    echo "pip check FAILED — kein Release" >&2
    "$PY" -m pip check >&2
    rm -rf "$STAGE"; exit 1
fi

echo "== 5/6 release.json ==" >&2
LOCK_SHA="$(sha256sum "$LOCK" | cut -d' ' -f1)"
PY_VERSION="$("$PY" -c 'import platform; print(platform.python_version())')"
DEP_MANIFEST="$("$PY" -m pip freeze | LC_ALL=C sort | sha256sum | cut -d' ' -f1)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
# Der Baum-Hash kommt aus DEMSELBEN Code, der ihn spaeter prueft — zwei
# Implementierungen desselben Hashes waeren zwei Wahrheiten.
TREE_SHA="$("$PY" -c "
import sys
sys.path.insert(0, '$STAGE')
from app.observability.release_identity import release_tree_sha256
from pathlib import Path
print(release_tree_sha256(Path('$STAGE')))
")" || { echo "Baum-Hash gescheitert" >&2; rm -rf "$STAGE"; exit 1; }

cat > "$STAGE/release.json" <<EOF
{
  "schema": "kai_release/v1",
  "repo_sha": "$REPO_SHA",
  "release_path": "$TARGET",
  "release_tree_sha256": "$TREE_SHA",
  "requirements_lock_sha256": "$LOCK_SHA",
  "python_version": "$PY_VERSION",
  "created_at_utc": "$NOW",
  "venv_python_path": "$TARGET/.venv/bin/python3",
  "dependency_manifest_sha256": "$DEP_MANIFEST",
  "builder_version": "$BUILDER_VERSION"
}
EOF

echo "== 6/6 versiegeln ==" >&2
mv "$STAGE" "$TARGET" || { echo "Umbenennen gescheitert" >&2; rm -rf "$STAGE"; exit 1; }
# Dass der Code-Baum nicht beschreibbar ist, ist Teil des Beweises. Die
# Zustands-Symlinks zeigen nach draussen und bleiben schreibbar.
chmod -R a-w "$TARGET/app" "$TARGET/config" "$TARGET/deploy" "$TARGET/scripts" 2>/dev/null
chmod a-w "$TARGET/requirements.lock" "$TARGET/pyproject.toml" "$TARGET/release.json" 2>/dev/null

# Selbstkontrolle: der versiegelte Baum muss seinen eigenen Anspruch tragen.
if ! "$TARGET/.venv/bin/python3" -c "
import sys
sys.path.insert(0, '$TARGET')
from app.observability.release_identity import verify_release
from pathlib import Path
p = verify_release(Path('$TARGET'))
sys.exit(0 if not p else 1)
"; then
    echo "Release traegt seinen eigenen Anspruch NICHT — nicht aktivieren" >&2
    exit 1
fi

echo "RELEASE_READY=$TARGET" >&2
echo "$TARGET"
exit 0
