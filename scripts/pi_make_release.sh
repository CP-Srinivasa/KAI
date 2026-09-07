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
#   bash scripts/pi_make_release.sh [--repo <checkout>] [--releases <dir>]
#                                  [--state <dir>] [--rebuild]
#
# `--rebuild` nur fuer den Fall RELEASE_TREE_MISMATCH: derselbe `repo_sha`,
# aber ein anderer Baum (praktisch immer ein neu gebautes `web/dist`). Ohne
# das Flag bricht der Builder ab, statt den alten Baum stillschweigend
# weiterzureichen; mit dem Flag baut er DANEBEN, unter `<SHA>-<tree8>`, und
# laesst das aktive Release unangetastet.
#
# Exit: 0 = Release gebaut und versiegelt (Pfad auf stdout) · 1 = gescheitert
#       (auch bei RELEASE_TREE_MISMATCH ohne --rebuild)
set -uo pipefail

BUILDER_VERSION="pi_make_release/1"
REPO="."
RELEASES=""
STATE=""
REBUILD=0
while [ $# -gt 0 ]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --releases) RELEASES="$2"; shift 2 ;;
        --state) STATE="$2"; shift 2 ;;
        --rebuild) REBUILD=1; shift ;;
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

# Der Code-Teil des Stagings, als Funktion -- denn die Idempotenz-Pruefung
# unten braucht denselben Baum ein zweites Mal, nur ohne venv. Zwei Kopien
# dieser Liste waeren zwei Wahrheiten darueber, was ein Release ausmacht.
stage_code() {
    local dest=$1
    rm -rf "$dest"
    mkdir -p "$dest" || return 1
    for d in app config deploy monitor scripts; do
        [ -d "$REPO/$d" ] && cp -a "$REPO/$d" "$dest/$d"
    done
    # Wurzel-Artefakte, die der laufende Code ueber ``parents[2]`` liest. Fehlt
    # CONFIG_SCHEMA.json, wirft bereits ``get_settings()`` -- das Release liesse
    # sich versiegeln und koennte trotzdem nicht starten (gemessen 2026-09-04).
    for f in requirements.lock pyproject.toml CONFIG_SCHEMA.json DECISION_SCHEMA.json alembic.ini; do
    [ -f "$REPO/$f" ] && cp -a "$REPO/$f" "$dest/$f"
    done
    # Die gebaute SPA ist Code, kein Zustand: `app/api/main.py` mountet sie ueber
    # das CWD-relative `web/dist`, und das CWD ist nach dem Cutover die
    # Release-Wurzel. Fehlt sie dort, verschwindet /dashboard STILL -- der Mount
    # steht hinter `if _spa_dir.is_dir()`, es gibt also weder Fehler noch Log.
    if [ -d "$REPO/web/dist" ]; then
    mkdir -p "$dest/web"
    cp -a "$REPO/web/dist" "$dest/web/dist"
    else
    echo "WARNUNG: $REPO/web/dist fehlt — dieses Release liefert KEIN Dashboard." >&2
    fi

    # Caches gehoeren nicht in eine Identitaet.
    find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
}

# Existiert das Ziel schon, entscheidet der BAUM, nicht der Pfad.
#
# Bis 2026-09-04 genuegte hier `[ -d "$TARGET" ] && exit 0`. Das war richtig,
# solange `repo_sha` den Baum bestimmte. Seit `web/dist` zur Identitaet gehoert
# (#873) stimmt das nicht mehr: die gebaute SPA ist gitignored und damit NICHT
# aus dem Commit ableitbar. Zwei verschiedene Baeume koennen sich denselben
# `repo_sha` teilen -- und der Builder gab den alten zurueck, ohne hinzusehen.
#
# Gemessen: Release b78872b0 trug `assets/index-DkDllgvZ.js`, gebaut VOR #848,
# waehrend sein eigener Code von DANACH stammte. `verify_release` blieb gruen,
# weil der Baum zu seinem eigenen Manifest passte -- nur eben nicht zum Commit.
#
# Die Probe ist billig: sie stellt nur den Code-Baum her (kein venv, der geht
# nicht in den Hash ein) und rechnet mit DEMSELBEN Code, der spaeter prueft.
if [ -d "$TARGET" ]; then
    PROBE="$RELEASES/.probe-$REPO_SHA.$$"
    trap 'rm -rf "$PROBE"' EXIT
    stage_code "$PROBE" || { echo "Probe-Staging gescheitert" >&2; exit 1; }
    NEW_TREE="$(python3 -c "
import sys
sys.path.insert(0, '$PROBE')
from app.observability.release_identity import release_tree_sha256
from pathlib import Path
print(release_tree_sha256(Path('$PROBE')))
")" || { echo "Probe-Hash gescheitert" >&2; exit 1; }
    OLD_TREE="$(python3 -c "
import json, sys
try:
    print(json.load(open('$TARGET/release.json'))['release_tree_sha256'])
except Exception:
    sys.exit(1)
")" || OLD_TREE=""
    rm -rf "$PROBE"; trap - EXIT

    if [ "$NEW_TREE" = "$OLD_TREE" ]; then
        echo "Release existiert bereits und ist baum-identisch: $TARGET" >&2
        echo "$TARGET"
        exit 0
    fi

    echo "RELEASE_TREE_MISMATCH: $TARGET traegt einen ANDEREN Baum als der" >&2
    echo "  aktuelle Arbeitsbaum unter demselben repo_sha." >&2
    echo "    vorhanden: ${OLD_TREE:-<unlesbar>}" >&2
    echo "    aktuell:   $NEW_TREE" >&2
    echo "  Ueblichste Ursache: web/dist wurde neu gebaut (gitignored, also nicht" >&2
    echo "  aus dem Commit ableitbar). Ein stilles Wiederverwenden waere eine" >&2
    echo "  Luege ueber den ausgelieferten Code." >&2
    if [ "$REBUILD" -eq 1 ]; then
        TARGET="$RELEASES/$REPO_SHA-${NEW_TREE:0:8}"
        STAGE="$RELEASES/.staging-$REPO_SHA.$$"
        if [ -d "$TARGET" ]; then
            echo "  --rebuild: $TARGET existiert bereits" >&2
            echo "$TARGET"
            exit 0
        fi
        echo "  --rebuild: baue nach $TARGET" >&2
    else
        echo "  --rebuild baut daneben, unter <SHA>-<tree8>." >&2
        exit 1
    fi
fi

echo "== 1/6 Code in die Staging-Flaeche ==" >&2
stage_code "$STAGE" || { echo "kann $STAGE nicht anlegen" >&2; exit 1; }

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
# Versiegelt wird, was SEALED_DIRS/SEALED_FILES als Identitaet fuehren --
# sonst waere ein Teil des Baum-Hashes schreibbar.
chmod -R a-w "$TARGET/app" "$TARGET/config" "$TARGET/deploy" "$TARGET/monitor" \n             "$TARGET/scripts" "$TARGET/web" 2>/dev/null
chmod a-w "$TARGET/requirements.lock" "$TARGET/pyproject.toml" "$TARGET/release.json" \n          "$TARGET/CONFIG_SCHEMA.json" "$TARGET/DECISION_SCHEMA.json" \n          "$TARGET/alembic.ini" 2>/dev/null

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

# Startfaehigkeit ist Teil der Identitaet, nicht Sache des Glueckens beim
# Restart. Ein Baum, der sich versiegeln laesst und beim ersten Start wirft,
# ist die gefaehrlichste Variante: `verify_release` sagt OK, die fuenf
# Daemons fallen trotzdem in die Restart-Schleife. Gemessen 2026-09-04 --
# CONFIG_SCHEMA.json war nicht gestaged, `verify_release` gruen,
# `get_settings()` warf beim ersten Start. Deshalb importiert der Builder
# hier, was der Dienst importiert: aus dem VERSIEGELTEN Baum, mit dessen
# EIGENEM venv.
SMOKE_LOG="$(mktemp)"
if ! (cd "$TARGET" && "$TARGET/.venv/bin/python3" -c "import app.api.main" >"$SMOKE_LOG" 2>&1); then
    echo "SMOKE_IMPORT_FAILED -- das Release startet nicht und wird nicht ausgeliefert:" >&2
    tail -20 "$SMOKE_LOG" >&2
    rm -f "$SMOKE_LOG"
    exit 1
fi
rm -f "$SMOKE_LOG"

echo "RELEASE_READY=$TARGET" >&2
echo "$TARGET"
exit 0
