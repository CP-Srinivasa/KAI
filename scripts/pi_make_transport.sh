#!/usr/bin/env bash
# scripts/pi_make_transport.sh — einen UNVERAENDERLICHEN Transport-Baum bauen.
#
# WARUM ES DIESEN BUILDER GIBT (ADR 0019):
#
#   `litellm[proxy]` verlangt `openai<3.0.0`, der KAI-Core faehrt `openai==3.6.0`.
#   Der Konflikt ist echt und in keiner LiteLLM-Version aufgeloest -- auch nicht
#   in der Entwicklungsschiene. Ein Transport, der bestimmt, welche
#   OpenAI-Version der Kern fahren darf, ist nicht austauschbar; er ist der Kern
#   mit einem anderen Namen.
#
#   KAI importiert `litellm` nirgends als Python-Paket. Der Proxy ist ein Dienst
#   hinter einem Socket (127.0.0.1:4000), kein Import. Deshalb kann sein Binary
#   in einem EIGENEN Baum liegen, mit eigenem Abhaengigkeitsvertrag.
#
# WAS DIESES SKRIPT NICHT TUT: es installiert keine Unit, startet keinen Dienst,
# schaltet keinen Symlink um und schreibt keinen Deploy-Marker. Es erzeugt ein
# Artefakt und attestiert es. Alles Weitere sind getrennte Tore (ADR 0019,
# „Status der Umsetzung").
#
# Usage:
#   bash scripts/pi_make_transport.sh [--repo <checkout>] [--transports <dir>]
#                                     [--extra <name>] [--force]
#
# `--extra` benennt die Gruppe in `pyproject.toml` (Vorgabe: litellm). Die
# VERSION steht dort, nicht hier: zwei Orte fuer dieselbe Version waeren zwei
# Wahrheiten.
#
# `--force` baut auch dann neu, wenn ein Baum mit derselben Spec schon existiert
# und sein Manifest traegt. Ohne das Flag ist ein zweiter Lauf billig.
#
# Exit: 0 = Baum gebaut und versiegelt (Pfad auf stdout) · 1 = gescheitert
set -uo pipefail

BUILDER_VERSION="pi_make_transport/1"
REPO="."
TRANSPORTS=""
EXTRA="litellm"
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --transports) TRANSPORTS="$2"; shift 2 ;;
        --extra) EXTRA="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        *) echo "unbekanntes Argument: $1" >&2; exit 1 ;;
    esac
done

REPO="$(cd "$REPO" 2>/dev/null && pwd)" || { echo "kein Checkout: $REPO" >&2; exit 1; }
[ -f "$REPO/pyproject.toml" ] || { echo "pyproject.toml fehlt in $REPO" >&2; exit 1; }

# BEWUSST NICHT unter `releases/`: dort raeumt `pi_activate_release.sh --keep N`
# auf. Ein Transport-Baum, den die Release-Rotation mitnimmt, faellt aus einem
# Grund aus, der ihn nichts angeht.
#
# Die Vorgabe haengt am HOME, nicht an `dirname "$REPO"`. Der Unterschied ist
# nicht kosmetisch: auf der Pi zeigt `current` nach `/home/ubuntu/releases/<sha>`,
# und `dirname` davon ist `/home/ubuntu/releases` -- die Vorgabe landete
# ausgerechnet dort, wo sie nicht hingehoert. Die Absicht stand im Kommentar,
# die Ableitung tat etwas anderes.
[ -n "$TRANSPORTS" ] || TRANSPORTS="${HOME:?HOME nicht gesetzt}/transport/$EXTRA"

# Auch ein ausdrueckliches --transports darf nicht dorthin zeigen. Symlinks
# werden vorher aufgeloest: auf der Pi ist `/home/kai` ein Symlink auf
# `/home/ubuntu`, und ein Pfad kann harmlos aussehen und trotzdem in der
# Rotation liegen.
# Aufgeloest wird VOR dem Anlegen: eine Wache, die ihren Ablehnungsgrund erst
# erschafft, hinterliesse bei jedem Fehlversuch ein Verzeichnis genau dort, wo
# keines hingehoert.
# `-m` statt `-f`: `-f` verlangt, dass alle Komponenten ausser der letzten
# existieren -- und beim ERSTEN Bau existiert `$HOME/transport` nicht. Der
# Lauf brach ab, bevor er etwas tat. `-m` loest trotzdem auf, Symlinks in
# vorhandenen Komponenten eingeschlossen: `/home/kai` ist auf der Pi einer.
WUNSCH="$TRANSPORTS"
TRANSPORTS="$(readlink -m "$TRANSPORTS")" || { echo "Pfad nicht aufloesbar: $WUNSCH" >&2; exit 1; }
[ -n "$TRANSPORTS" ] || { echo "Pfad nicht aufloesbar: $WUNSCH" >&2; exit 1; }
case "$TRANSPORTS" in
    */releases|*/releases/*)
        echo "TRANSPORT_PATH_IN_RELEASE_ROTATION: $TRANSPORTS" >&2
        echo "  Dort raeumt \`pi_activate_release.sh --keep N\` auf. Ein anderer Pfad." >&2
        exit 1
        ;;
esac
mkdir -p "$TRANSPORTS" || { echo "kann $TRANSPORTS nicht anlegen" >&2; exit 1; }

# --- die Spec kommt aus pyproject.toml, nicht von hier -----------------------
SPEC="$(python3 -c '
import sys, tomllib

with open(sys.argv[1], "rb") as fh:
    verfuegbar = tomllib.load(fh)["project"].get("optional-dependencies", {})
name = sys.argv[2]
if name not in verfuegbar:
    print(f"UNBEKANNTES_EXTRA {name} (verfuegbar: {sorted(verfuegbar)})", file=sys.stderr)
    raise SystemExit(1)
for spec in verfuegbar[name]:
    print(spec)
' "$REPO/pyproject.toml" "$EXTRA")" || { echo "Spec-Aufloesung gescheitert" >&2; exit 1; }

[ -n "$SPEC" ] || { echo "Extra '$EXTRA' ist leer" >&2; exit 1; }
SPEC_SHA="$(printf '%s\n' $SPEC | LC_ALL=C sort | sha256sum | cut -d' ' -f1)"
echo "Transport: $EXTRA" >&2
printf '  %s\n' $SPEC >&2

# --- billige Idempotenz-Probe ------------------------------------------------
#
# Der endgueltige Pfad haengt am Dependency-Manifest, und das ist erst NACH dem
# Bau bekannt. Ein venv mit ~670 MB nur zu bauen, um ihn wegzuwerfen, waere
# teuer. Deshalb wird zuerst gefragt, ob ein Baum mit DERSELBEN Spec existiert
# und sein Manifest noch traegt -- dann ist er die Antwort.
#
# Das heisst ausdruecklich: der ERSTE Bau gewinnt. `litellm[proxy]==1.99.0` zieht
# transitive Pakete, die in keinem Lock stehen und "neuestes zum Bauzeitpunkt"
# aufloesen; ein zweiter Lauf soll sie NICHT stillschweigend anheben. Wer etwas
# Neueres will, aendert die Spec oder nimmt `--force`.
if [ "$FORCE" -eq 0 ]; then
    VORHANDEN="$(python3 -c '
import json, pathlib, sys

wurzel = pathlib.Path(sys.argv[1])
gesuchte_spec = sys.argv[2]
for kandidat in sorted(wurzel.glob("*/transport.json")):
    try:
        with kandidat.open(encoding="utf-8") as fh:
            daten = json.load(fh)
    except Exception:
        continue
    if daten.get("spec_sha256") == gesuchte_spec:
        print(kandidat.parent)
        break
' "$TRANSPORTS" "$SPEC_SHA")" || VORHANDEN=""

    if [ -n "$VORHANDEN" ] && [ -x "$VORHANDEN/.venv/bin/python3" ]; then
        IST="$("$VORHANDEN/.venv/bin/python3" -m pip freeze 2>/dev/null | LC_ALL=C sort | sha256sum | cut -d' ' -f1)"
        SOLL="$(python3 -c '
import json, sys

with open(sys.argv[1], encoding="utf-8") as fh:
    print(json.load(fh).get("dependency_manifest_sha256", ""))
' "$VORHANDEN/transport.json")" || SOLL=""
        if [ -n "$SOLL" ] && [ "$IST" = "$SOLL" ]; then
            echo "Transport existiert bereits und traegt sein Manifest: $VORHANDEN" >&2
            echo "$VORHANDEN"
            exit 0
        fi
        echo "TRANSPORT_DEPENDENCY_DRIFT in $VORHANDEN" >&2
        echo "    aufgezeichnet: ${SOLL:-<unlesbar>}" >&2
        echo "    tatsaechlich:  $IST" >&2
        echo "  Der Baum ist nachtraeglich veraendert worden. Ein neuer Bau" >&2
        echo "  wuerde ihn nicht heilen -- er steht daneben. --force baut neu." >&2
        exit 1
    fi
fi

STAGE="$TRANSPORTS/.staging-$$"
trap 'rm -rf "$STAGE"' EXIT
rm -rf "$STAGE"
mkdir -p "$STAGE" || { echo "kann $STAGE nicht anlegen" >&2; exit 1; }

echo "== 1/5 eigener venv, OHNE den Core-Lock ==" >&2
# Kein `-c requirements.lock`: das ist der ganze Punkt von ADR 0019. Der
# Transport loest seine Abhaengigkeiten selbst auf; der Core-Vertrag gilt fuer
# den Core. Ein Constraint hier holte den Konflikt zurueck, den die Trennung
# gerade beseitigt.
python3 -m venv "$STAGE/.venv" || { echo "venv-Bau gescheitert" >&2; exit 1; }
PY="$STAGE/.venv/bin/python3"
"$PY" -m pip install --upgrade pip >/dev/null 2>&1
PIP_LOG="/tmp/kai-transport-pip.$$.log"
if ! "$PY" -m pip install $SPEC >"$PIP_LOG" 2>&1; then
    echo "Installation gescheitert - siehe $PIP_LOG" >&2
    tail -15 "$PIP_LOG" >&2
    exit 1
fi

echo "== 2/5 pip check ==" >&2
if ! "$PY" -m pip check >/dev/null 2>&1; then
    echo "pip check FAILED - kein Transport" >&2
    "$PY" -m pip check >&2
    exit 1
fi

echo "== 3/5 eigener Lock und Manifest ==" >&2
# Der EIGENE Lock des Baums: was tatsaechlich drin ist, eingefroren. Er
# beschreibt diesen Transport, nicht KAI -- und er ist der Grund, warum ein
# Restore kein Netz braucht.
"$PY" -m pip freeze | LC_ALL=C sort > "$STAGE/requirements.lock"
DEP_MANIFEST="$(LC_ALL=C sort < "$STAGE/requirements.lock" | sha256sum | cut -d' ' -f1)"
VERSION="$("$PY" -c "
import importlib.metadata as md
import sys
try:
    print(md.version('$EXTRA'))
except Exception:
    sys.exit(1)
")" || { echo "Version von '$EXTRA' nicht ermittelbar" >&2; exit 1; }
PY_VERSION="$("$PY" -c 'import platform; print(platform.python_version())')"
BINARY="$STAGE/.venv/bin/$EXTRA"
[ -x "$BINARY" ] || { echo "TRANSPORT_BINARY_MISSING ($EXTRA)" >&2; exit 1; }

TARGET="$TRANSPORTS/$VERSION-${DEP_MANIFEST:0:8}"
if [ -d "$TARGET" ] && [ "$FORCE" -eq 0 ]; then
    echo "Baum existiert bereits: $TARGET" >&2
    echo "$TARGET"
    exit 0
fi
[ "$FORCE" -eq 1 ] && rm -rf "$TARGET"

SPEC_JSON="$(printf '%s\n' $SPEC | sed 's/.*/"&"/' | paste -sd, -)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
cat > "$STAGE/transport.json" <<EOF
{
  "schema": "kai_transport/v1",
  "transport": "$EXTRA",
  "version": "$VERSION",
  "spec": [$SPEC_JSON],
  "spec_sha256": "$SPEC_SHA",
  "requirements_lock_sha256": "$(sha256sum "$STAGE/requirements.lock" | cut -d' ' -f1)",
  "dependency_manifest_sha256": "$DEP_MANIFEST",
  "python_version": "$PY_VERSION",
  "binary_path": "$TARGET/.venv/bin/$EXTRA",
  "created_at_utc": "$NOW",
  "builder_version": "$BUILDER_VERSION"
}
EOF

echo "== 4/5 keine Geheimnisse im Artefakt ==" >&2
# Die Config bleibt im KAI-Release; hier darf nichts liegen, was jemand
# spaeter mit einem Backup weiterreicht. Geprueft wird der Baum OHNE venv --
# in Paket-Metadaten stehen fremde Beispielschluessel, und ein Fehlalarm
# darueber wuerde die Pruefung entwerten.
FUNDE="$(grep -rlE 'sk-[A-Za-z0-9]{16,}|API_KEY[[:space:]]*=|MASTER_KEY[[:space:]]*=' \
    --exclude-dir=.venv "$STAGE" 2>/dev/null | head -5)"
if [ -n "$FUNDE" ]; then
    echo "SECRET_IN_ARTIFACT:" >&2
    printf '  %s\n' $FUNDE >&2
    exit 1
fi
for verboten in .env .env.local id_rsa; do
    [ -e "$STAGE/$verboten" ] && { echo "SECRET_IN_ARTIFACT: $verboten" >&2; exit 1; }
done

echo "== 5/5 versiegeln ==" >&2
# Nach dem `mv` traegt der Baum seinen endgueltigen Namen. Schlaegt eine der
# beiden Kontrollen danach fehl, darf er dort NICHT liegen bleiben: sein
# Manifest waere gueltig, und der naechste Lauf haelte ihn fuer fertig -- genau
# das "sieht aus wie ein Artefakt und ist keines", gegen das das atomare `mv`
# antritt. Verworfene Baeume wandern eine Ebene tiefer nach `rejected/`, wo die
# Suche (`*/transport.json`) sie nicht mehr sieht, die Diagnose aber schon.
_verwerfen() {
    ABLAGE="$TRANSPORTS/rejected/$(basename "$TARGET").$$"
    mkdir -p "$TRANSPORTS/rejected"
    mv "$TARGET" "$ABLAGE" 2>/dev/null || rm -rf "$TARGET"
    echo "  verworfen nach: $ABLAGE" >&2
    exit 1
}
mv "$STAGE" "$TARGET" || { echo "Umbenennen gescheitert" >&2; exit 1; }
trap - EXIT
# Manifest und Lock sind die Identitaet -- waeren sie beschreibbar, waere sie es
# auch. Der venv bleibt schreibbar: `pip` legt beim Start Bytecode an, und ein
# nur-lesbarer venv scheiterte daran. Gegen nachtraegliche Aenderung schuetzt
# nicht das Dateirecht, sondern der Manifest-Abgleich unten und beim Start.
# Ein venv ist NICHT verschiebbar. `pip` backt beim Installieren den absoluten
# Pfad des Interpreters in die Shebang jeder Konsolen-Anwendung; `activate` und
# `pyvenv.cfg` tragen ihn ebenfalls. Nach dem `mv` zeigt die Shebang auf das
# Staging, das es nicht mehr gibt -- die Datei ist da, ist ausfuehrbar, und
# `execve` scheitert am Interpreter. Die Fehlermeldung lautet dann "No such file
# or directory" und nennt die Datei, die existiert.
#
# Warum trotzdem ueber ein Staging gebaut wird: der endgueltige Name haengt am
# Dependency-Manifest, und das ist erst NACH der Installation bekannt. Der
# Zielpfad laesst sich vorher nicht bilden. Also: bauen, benennen, reparieren.
echo "   Pfade im venv auf den Zielort umschreiben" >&2
for datei in "$TARGET/.venv/bin"/*; do
    [ -f "$datei" ] || continue
    grep -Iq . "$datei" 2>/dev/null || continue   # Binaerdateien auslassen
    sed -i "s|$STAGE/.venv|$TARGET/.venv|g" "$datei"
done
sed -i "s|$STAGE/.venv|$TARGET/.venv|g" "$TARGET/.venv/pyvenv.cfg"

# Fail-closed: bleibt irgendwo im ausfuehrbaren Teil ein Staging-Pfad stehen,
# ist der Baum unbrauchbar, und das soll hier auffallen und nicht beim Start.
REST="$(grep -rlI -- "$STAGE" "$TARGET/.venv/bin" "$TARGET/.venv/pyvenv.cfg" 2>/dev/null | head -5)"
if [ -n "$REST" ]; then
    echo "TRANSPORT_STAGING_PATH_LEFTOVER -- Pfade zeigen noch ins Staging:" >&2
    echo "$REST" >&2
    _verwerfen
fi

chmod a-w "$TARGET/transport.json" "$TARGET/requirements.lock" 2>/dev/null

# Selbstkontrolle: der versiegelte Baum muss seinen eigenen Anspruch tragen.
IST="$("$TARGET/.venv/bin/python3" -m pip freeze 2>/dev/null | LC_ALL=C sort | sha256sum | cut -d' ' -f1)"
if [ "$IST" != "$DEP_MANIFEST" ]; then
    echo "TRANSPORT_MANIFEST_MISMATCH nach dem Versiegeln" >&2
    echo "    aufgezeichnet: $DEP_MANIFEST" >&2
    echo "    tatsaechlich:  $IST" >&2
    _verwerfen
fi

# Startfaehigkeit ist Teil der Identitaet. Ein Baum, der sich versiegeln laesst
# und beim ersten Start wirft, ist die gefaehrlichste Variante -- beim Release
# ist genau das am 2026-09-04 passiert. Kein Server, kein Port: nur die Frage,
# ob das Binary ueberhaupt laeuft.
SMOKE_LOG="$(mktemp)"
if ! timeout 180 "$TARGET/.venv/bin/$EXTRA" --version >"$SMOKE_LOG" 2>&1; then
    echo "TRANSPORT_SMOKE_FAILED -- das Binary startet nicht:" >&2
    tail -20 "$SMOKE_LOG" >&2
    rm -f "$SMOKE_LOG"
    _verwerfen
fi
rm -f "$SMOKE_LOG" "$PIP_LOG"

echo "TRANSPORT_READY=$TARGET" >&2
echo "  version    $VERSION" >&2
echo "  manifest   $DEP_MANIFEST" >&2
echo "$TARGET"
exit 0
