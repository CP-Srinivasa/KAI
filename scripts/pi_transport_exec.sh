#!/usr/bin/env bash
# Startet ein Binary aus einem attestierten Transport-Baum -- oder gar nicht.
#
# ADR 0019 trennt die Transport-Runtime vom KAI-Release: `litellm[proxy]`
# verlangt `openai<3.0.0`, der Kern faehrt `openai==3.6.0`. Damit liegt das
# Binary NICHT mehr im Release-venv, und die Unit braucht einen Weg dorthin,
# der nicht "irgendein litellm" startet.
#
# Die Kette ist bewusst zweistufig und jede Stufe besitzt ihren eigenen Baum:
#
#   systemd
#     -> `runtime-exec --repo /home/kai/current`   attestiert den RELEASE
#          -> dieses Skript (liegt IM Release, faellt also unter dessen Identitaet)
#               -> attestiert den TRANSPORT
#                    -> exec des Binaries
#
# Die Control-Plane wird dabei nicht dupliziert: hier entsteht keine Politik,
# keine Konfiguration und kein Zustand. Es wird geprueft und exec't.
#
# KEIN PATH, KEINE UMGEBUNGSVARIABLE, KEIN CHECKOUT-venv. Der Wurzelpfad steht
# hier fest. Eine Variable, die ihn verschieben koennte, waere genau der Weg,
# einen anderen Prozess unter diesem Namen zu starten -- und der Sinn der
# Attestierung waere weg.
set -uo pipefail

TRANSPORTS_ROOT="/home/kai/transport"
# Der System-Interpreter liest das Manifest -- absichtlich NICHT der des
# Baumes, der hier gerade beurteilt wird, und absichtlich nicht ueber den PATH.
PY_SYSTEM="/usr/bin/python3"

NAME="${1:-}"
[ -n "$NAME" ] || { echo "Aufruf: $0 <transport> [args...]" >&2; exit 2; }
shift

_ab() { echo "$1" >&2; exit 1; }

[ -x "$PY_SYSTEM" ] || _ab "TRANSPORT_NO_SYSTEM_PYTHON: $PY_SYSTEM"

# --- 1. der Baum ------------------------------------------------------------
# `current` ist ein Zeiger; aufgeloest wird er sofort, und ab hier gilt nur noch
# der aufgeloeste Pfad. Ein Zeiger, der waehrend des Laufs umgehaengt wird, darf
# nicht bedeuten, dass ein anderer Baum lief als der attestierte.
TREE="$(readlink -f "$TRANSPORTS_ROOT/$NAME/current" 2>/dev/null)"
[ -n "$TREE" ] && [ -d "$TREE" ] || _ab "TRANSPORT_NOT_INSTALLED: $TRANSPORTS_ROOT/$NAME/current"

MANIFEST="$TREE/transport.json"
[ -r "$MANIFEST" ] || _ab "TRANSPORT_MANIFEST_UNREADABLE: $MANIFEST"

# --- 2. das Manifest beschreibt DIESEN Baum ---------------------------------
FELDER="$("$PY_SYSTEM" -c '
import json, sys

with open(sys.argv[1], encoding="utf-8") as fh:
    d = json.load(fh)
for schluessel in ("transport", "version", "binary_path", "dependency_manifest_sha256"):
    print(d.get(schluessel, ""))
' "$MANIFEST" 2>/dev/null)" || _ab "TRANSPORT_MANIFEST_UNREADABLE: $MANIFEST"

TRANSPORT_NAME="$(printf '%s\n' "$FELDER" | sed -n 1p)"
VERSION="$(printf '%s\n' "$FELDER" | sed -n 2p)"
BINARY="$(printf '%s\n' "$FELDER" | sed -n 3p)"
SOLL="$(printf '%s\n' "$FELDER" | sed -n 4p)"

[ "$TRANSPORT_NAME" = "$NAME" ] || _ab "TRANSPORT_NAME_MISMATCH: Manifest sagt '$TRANSPORT_NAME', verlangt war '$NAME'"
[ -n "$SOLL" ] || _ab "TRANSPORT_MANIFEST_UNREADABLE: kein dependency_manifest_sha256"

# Ein Manifest, das auf einen anderen Baum zeigt, ist kopiert und beschreibt
# nicht, was hier liegt.
case "$BINARY" in
    "$TREE"/*) : ;;
    *) _ab "TRANSPORT_MANIFEST_FOREIGN: binary_path '$BINARY' liegt nicht in $TREE" ;;
esac
[ -x "$BINARY" ] || _ab "TRANSPORT_BINARY_MISSING: $BINARY"

# --- 3. der Interpreter der Shebang muss es geben ---------------------------
# Am 2026-09-08 hat genau das gefehlt: `pip` backt den absoluten Interpreterpfad
# in jede Konsolen-Anwendung, und nach einem Verschieben zeigte er ins Leere.
# `execve` meldet dann "No such file or directory" ueber eine Datei, die es
# gibt -- eine Meldung, die in die Irre fuehrt. Hier faellt es vorher auf.
KOPF="$(head -c 256 "$BINARY" 2>/dev/null | sed -n '1s/^#!\([^ ]*\).*/\1/p')"
if [ -n "$KOPF" ]; then
    [ -x "$KOPF" ] || _ab "TRANSPORT_INTERPRETER_MISSING: Shebang zeigt auf '$KOPF' -- das gibt es nicht"
    case "$KOPF" in
        */transport/*)
            case "$KOPF" in
                "$TREE"/*) : ;;
                *) _ab "TRANSPORT_INTERPRETER_FOREIGN: Shebang zeigt in einen anderen Baum: $KOPF" ;;
            esac
            ;;
    esac
fi

# --- 4. der Baum traegt sein Manifest ---------------------------------------
# 0,4 s auf kai-pi5, gemessen. Der Start des Binaries selbst braucht 9 s.
IST="$("$TREE/.venv/bin/python3" -m pip freeze 2>/dev/null | LC_ALL=C sort | sha256sum | cut -d' ' -f1)"
if [ "$IST" != "$SOLL" ]; then
    echo "TRANSPORT_DEPENDENCY_DRIFT in $TREE" >&2
    echo "    aufgezeichnet: $SOLL" >&2
    echo "    tatsaechlich:  ${IST:-<nicht ermittelbar>}" >&2
    echo "  Der Baum ist nicht mehr der attestierte. Kein Start." >&2
    exit 1
fi

# Die Provenienz gehoert ins Log, nicht nur in die Pruefung: wer spaeter fragt,
# welcher Baum lief, soll es lesen koennen und nicht rekonstruieren muessen.
echo "TRANSPORT_VERIFIED name=$NAME version=$VERSION tree=$TREE manifest=${SOLL:0:16}" >&2

exec "$BINARY" "$@"
