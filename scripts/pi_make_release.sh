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
#                                  [--allow-missing-spa] [--extra <name> ...]
#
# `--extra` installiert eine optionale Abhaengigkeitsgruppe aus `pyproject.toml`
# ZUSAETZLICH zum Lockfile, vor `pip check` und vor dem Versiegeln. Sie wird in
# `release.json` unter `extras` ausgewiesen, und der Release landet unter
# `<SHA>+<extra>` -- ein Release mit Extra steht NEBEN einem ohne.
#
# Warum das noetig ist: `release_tree_sha256` schliesst den venv ausdruecklich
# aus, `requirements_lock_sha256` kennt nur das Lockfile. Ohne eigenen Pfad und
# eigenes Feld waeren zwei Releases mit demselben Code und demselben Lock, aber
# verschiedenem venv, an genau den Feldern nicht zu unterscheiden, die zur
# Unterscheidung da sind -- und der Builder gaebe den vorhandenen zurueck.
#
# `--rebuild` nur fuer den Fall RELEASE_TREE_MISMATCH: derselbe `repo_sha`,
# aber ein anderer Baum (praktisch immer ein neu gebautes `web/dist`). Ohne
# das Flag bricht der Builder ab, statt den alten Baum stillschweigend
# weiterzureichen; mit dem Flag baut er DANEBEN, unter `<SHA>-<tree8>`, und
# laesst das aktive Release unangetastet.
#
# `--allow-missing-spa` baut ein Release OHNE Dashboard. Ohne das Flag bricht
# der Bau ab, wenn `web/dist` fehlt -- ein Release, das unter /dashboard
# schweigt, entsteht nur noch auf ausdrueckliche Ansage.
#
# Exit: 0 = Release gebaut und versiegelt (Pfad auf stdout) · 1 = gescheitert
#       (auch bei RELEASE_TREE_MISMATCH ohne --rebuild und bei SPA_MISSING)
set -uo pipefail

BUILDER_VERSION="pi_make_release/1"
REPO="."
RELEASES=""
STATE=""
REBUILD=0
ALLOW_MISSING_SPA=0
EXTRAS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --releases) RELEASES="$2"; shift 2 ;;
        --state) STATE="$2"; shift 2 ;;
        --rebuild) REBUILD=1; shift ;;
        --allow-missing-spa) ALLOW_MISSING_SPA=1; shift ;;
        --extra) EXTRAS="$EXTRAS $2"; shift 2 ;;
        *) echo "unbekanntes Argument: $1" >&2; exit 1 ;;
    esac
done
# Sortiert und dublettenfrei: die Reihenfolge auf der Kommandozeile darf die
# Identitaet des Releases nicht beeinflussen.
if [ -n "$EXTRAS" ]; then
    EXTRAS="$(printf '%s
' $EXTRAS | LC_ALL=C sort -u | tr '
' ' ' | sed 's/ *$//')"
fi

REPO="$(cd "$REPO" 2>/dev/null && pwd)" || { echo "kein Checkout: $REPO" >&2; exit 1; }
[ -n "$RELEASES" ] || RELEASES="$(dirname "$REPO")/releases"
[ -n "$STATE" ] || STATE="$REPO"

REPO_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null)" || {
    echo "kein Git-Checkout: $REPO" >&2; exit 1; }
LOCK="$REPO/requirements.lock"
[ -f "$LOCK" ] || { echo "requirements.lock fehlt" >&2; exit 1; }

# Ein Extra, das es nicht gibt, ist ein Tippfehler -- und einer, der still zu
# einem Release ohne das erwartete Paket fuehrt, faellt erst auf, wenn die Unit
# nicht startet. Deshalb vorher pruefen, gegen pyproject.toml als einzige
# Quelle: die Versionspins stehen dort, nicht hier.
EXTRA_SPECS=""
EXTRAS_SHA=""
if [ -n "$EXTRAS" ]; then
    EXTRA_SPECS="$(python3 -c '
import sys, tomllib

with open(sys.argv[1], "rb") as fh:
    verfuegbar = tomllib.load(fh)["project"].get("optional-dependencies", {})
specs = []
for name in sys.argv[2:]:
    if name not in verfuegbar:
        print(f"UNBEKANNTES_EXTRA {name} (verfuegbar: {sorted(verfuegbar)})", file=sys.stderr)
        raise SystemExit(1)
    specs.extend(verfuegbar[name])
print("
".join(specs))
' "$REPO/pyproject.toml" $EXTRAS)" || { echo "Extra-Aufloesung gescheitert" >&2; exit 1; }
    echo "Extras: $EXTRAS" >&2
    printf '  %s
' $EXTRA_SPECS >&2
    # Gehasht wird, was TATSAECHLICH installiert wird, nicht wie es heisst.
    # `litellm` mit ==1.99.0 und `litellm` mit ==2.0.0 tragen denselben Namen
    # und muessen trotzdem verschiedene Releases sein -- dieselbe Logik wie bei
    # `<SHA>-<tree8>`: nicht "was war gemeint", sondern "was ist drin".
    EXTRAS_SHA="$(printf '%s
' $EXTRA_SPECS | LC_ALL=C sort | sha256sum | cut -d' ' -f1)"
fi

# Ein Release MIT Extras steht NEBEN einem ohne, nicht darueber. Der Suffix ist
# kein Schmuck: `release_tree_sha256` schliesst den venv ausdruecklich aus, und
# `requirements_lock_sha256` kennt nur das Lockfile. Zwei Releases mit demselben
# Code und demselben Lock, aber verschiedenem venv, waeren an beiden Feldern
# nicht zu unterscheiden -- und die Idempotenz-Pruefung unten haette den zweiten
# Bau als "baum-identisch" abgewiesen und den ERSTEN zurueckgegeben. Still, ohne
# RELEASE_TREE_MISMATCH, ohne Hinweis auf --rebuild.
RELEASE_ID="$REPO_SHA"
if [ -n "$EXTRAS" ]; then
    # Name UND Hash: der Name macht den Pfad lesbar, der Hash macht ihn eindeutig.
    RELEASE_ID="$REPO_SHA+$(printf '%s' "$EXTRAS" | tr ' ' '+')-${EXTRAS_SHA:0:8}"
fi
TARGET="$RELEASES/$RELEASE_ID"
STAGE="$RELEASES/.staging-$RELEASE_ID.$$"

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
    #
    # ABBRUCH, NICHT WARNUNG (2026-09-07).
    #
    # Hier stand eine Warnung auf stderr. Jede andere Stufe dieses Builders
    # bricht ab -- fehlende Wurzel-Artefakte, `pip check`, Baum-Hash,
    # Smoke-Import -- und ausgerechnet die SPA war eine Notiz im Bau-Log. Genau
    # die Sorte Hinweis, die beim Cutover untergeht.
    #
    # Der Anlass ist real: ein frischer Worktree bringt `web/dist` nie mit (es
    # ist gitignored), also traf es den naechsten Hotfix-Baum sofort. Ohne
    # Abbruch waere ein Release entstanden, das startet, gruen verifiziert und
    # unter /dashboard schweigt -- der Mount steht hinter `if _spa_dir.is_dir()`,
    # es gibt weder Fehler noch Log. Dieselbe Klasse wie das fehlende
    # CONFIG_SCHEMA.json, nur leiser: nicht ein Release, das durchfaellt,
    # sondern eines, das unbemerkt weniger kann.
    if [ -d "$REPO/web/dist" ]; then
        mkdir -p "$dest/web"
        cp -a "$REPO/web/dist" "$dest/web/dist"
    elif [ "$ALLOW_MISSING_SPA" -eq 1 ]; then
        echo "SPA_MISSING_ACCEPTED: $REPO/web/dist fehlt, --allow-missing-spa gesetzt." >&2
        echo "  Dieses Release liefert KEIN Dashboard. Das ist jetzt eine getippte" >&2
        echo "  Entscheidung und steht so im Bau-Protokoll." >&2
    else
        echo "SPA_MISSING: $REPO/web/dist fehlt -- dieses Release wuerde KEIN" >&2
        echo "  Dashboard ausliefern, und zwar ohne Fehler und ohne Log." >&2
        echo "  web/dist ist gitignored, ein frischer Worktree bringt es nicht mit." >&2
        echo "" >&2
        if command -v npm >/dev/null 2>&1; then
            echo "  Bauen:" >&2
            echo "    cd $REPO/web && npm ci && npm run build" >&2
            echo "  Oder aus einem vorhandenen Baum uebernehmen:" >&2
        else
            # Auf der Pi gibt es kein npm (gemessen 2026-09-07). Dort ist
            # Kopieren nicht die Alternative, sondern der einzige Weg -- ein
            # Bau-Hinweis waere hier eine Sackgasse mit Anleitung.
            echo "  Auf diesem Host gibt es kein npm, bauen faellt also aus." >&2
            echo "  Uebernimm die SPA aus einem Baum, der sie hat:" >&2
        fi
        echo "    cp -a <quelle>/web/dist $REPO/web/dist" >&2
        echo "    (z. B. aus dem aktiven Release: readlink -f <current>)" >&2
        echo "" >&2
        echo "  Oder ausdruecklich verzichten: --allow-missing-spa" >&2
        return 1
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

    # Der Baum allein genuegt NICHT. `release_tree_sha256` schliesst den venv
    # aus (siehe release_identity.py), also sind zwei Releases mit gleichem Code
    # und unterschiedlichen Extras baum-identisch -- und der Builder gaebe den
    # falschen zurueck. Beide Merkmale muessen stimmen.
    # Verglichen wird der Hash ueber die aufgeloesten Specs, nicht die Namen:
    # sonst gaelten zwei Releases mit demselben Extra-Namen und verschiedenen
    # Versionen als identisch.
    OLD_EXTRAS="$(python3 -c '
import json, sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        print(json.load(fh).get("extras_sha256", ""))
except Exception:
    sys.exit(1)
' "$TARGET/release.json")" || OLD_EXTRAS=""

    if [ "$NEW_TREE" = "$OLD_TREE" ] && [ "$OLD_EXTRAS" = "$EXTRAS_SHA" ]; then
        echo "Release existiert bereits und ist baum-identisch: $TARGET" >&2
        echo "$TARGET"
        exit 0
    fi
    if [ "$NEW_TREE" = "$OLD_TREE" ]; then
        echo "RELEASE_EXTRAS_MISMATCH: $TARGET traegt andere Extras." >&2
        echo "    vorhanden: ${OLD_EXTRAS:-<keine>}" >&2
        echo "    verlangt:  ${EXTRAS_SHA:-<keine>} (${EXTRAS:-<keine>})" >&2
        echo "  Der Code-Baum ist identisch, der venv nicht. Ein stilles" >&2
        echo "  Wiederverwenden waere eine Luege ueber die installierten Pakete." >&2
        exit 1
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
# Der Grund steht schon in stage_code -- hier nur noch, dass es daran lag.
# "kann nicht anlegen" waere jetzt falsch: die haeufigste Ursache ist eine
# fehlende SPA, nicht ein Verzeichnisproblem.
stage_code "$STAGE" || { echo "Staging gescheitert (Grund oben) — kein Release" >&2; exit 1; }

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

if [ -n "$EXTRA_SPECS" ]; then
    # Vor `pip check`, nicht danach: ein Extra, das mit dem Lockfile in Konflikt
    # steht, soll den Bau abbrechen und nicht als versiegeltes Release
    # herauskommen, dessen Abhaengigkeiten sich widersprechen.
    if ! "$PY" -m pip install $EXTRA_SPECS >>/tmp/kai-release-pip.$$.log 2>&1; then
        echo "Extra-Installation gescheitert - siehe /tmp/kai-release-pip.$$.log" >&2
        rm -rf "$STAGE"; exit 1
    fi
fi

echo "== 4/6 pip check ==" >&2
if ! "$PY" -m pip check >/dev/null 2>&1; then
    echo "pip check FAILED — kein Release" >&2
    "$PY" -m pip check >&2
    rm -rf "$STAGE"; exit 1
fi

echo "== 5/6 release.json ==" >&2
LOCK_SHA="$(sha256sum "$LOCK" | cut -d' ' -f1)"
# Ein Feld, das den Unterschied BENENNT, statt einer Pruefsumme, die ihn nur
# bemerkt. `dependency_manifest_sha256` traegt die Extras zwar mit (es kommt aus
# `pip freeze`), sagt aber nicht, WORAN es liegt.
EXTRAS_JSON=""
EXTRA_SPECS_JSON=""
if [ -n "$EXTRAS" ]; then
    EXTRAS_JSON="$(printf '%s
' $EXTRAS | sed 's/.*/"&"/' | paste -sd, -)"
    EXTRA_SPECS_JSON="$(printf '%s
' $EXTRA_SPECS | LC_ALL=C sort | sed 's/.*/"&"/' | paste -sd, -)"
fi
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
  "extras": [$EXTRAS_JSON],
  "extra_specs": [$EXTRA_SPECS_JSON],
  "extras_sha256": "$EXTRAS_SHA",
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
