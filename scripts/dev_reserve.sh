#!/usr/bin/env bash
# Entwicklerreserve (ADR 0020, D-CORE-009): der zweite LiteLLM-Proxy und
# seine Fail-closed-Probe. Kein Teil der KAI-Laufzeit, keine Unit, kein Timer.
#
#   bash scripts/dev_reserve.sh proxy    (auf der Pi)  startet den Dev-Proxy
#                                        aus dem attestierten Transport auf
#                                        127.0.0.1:4001 -- Vordergrund, Ctrl-C beendet
#   bash scripts/dev_reserve.sh smoke    (vom Laptop, durch den Tunnel) eine
#                                        Mini-Anfrage je Route; FAIL_CLOSED, wenn
#                                        Modellidentitaet oder Kostenmessung fehlt
#   bash scripts/dev_reserve.sh tunnel   gibt den SSH-Tunnel-Befehl aus
#
# Sicherheitsvertrag:
#   * Der Proxy bekommt NUR `LITELLM_DEV_MASTER_KEY` und `KAI_DEV_LITELLM_*`.
#     Die .env wird NICHT als Ganzes exportiert -- das gaebe der Reserve jeden
#     Produktionsschluessel.
#   * Host ist fest 127.0.0.1. Der Laptop erreicht den Port nur per SSH-Tunnel.
#   * Der Client (OpenCode) kennt nur `KAI_DEV_LITELLM_KEY`; der Laufzeit-
#     `LITELLM_MASTER_KEY` verlaesst die Pi nicht.
set -uo pipefail

PORT=4001
HOST=127.0.0.1
ROUTEN_STANDARD="kai-dev-economy kai-dev-code"
ROUTEN_ALLE="kai-dev-economy kai-dev-code kai-dev-frontier"

_ab() { echo "$1" >&2; exit 1; }

cmd_proxy() {
    local repo="${KAI_DEV_RESERVE_REPO:-/home/kai/current}"
    local env_datei="${KAI_DEV_RESERVE_ENV:-/home/kai/ai_analyst_trading_bot/.env}"
    local exec_skript="$repo/scripts/pi_transport_exec.sh"
    local konfig="$repo/config/litellm_dev.yaml"

    [ -r "$env_datei" ] || _ab "DEV_RESERVE_NO_ENV: $env_datei nicht lesbar"
    [ -x "$exec_skript" ] || _ab "DEV_RESERVE_NO_TRANSPORT_EXEC: $exec_skript (Release aelter als ADR 0019?)"
    [ -r "$konfig" ] || _ab "DEV_RESERVE_NO_CONFIG: $konfig (Release ohne ADR 0020)"

    # Nur die Dev-Variablen. Zeile fuer Zeile, Muster auf den NAMEN, Wert ohne
    # umschliessende Anfuehrungszeichen und ohne CR (Windows-Editor). Alles
    # andere in der .env bleibt draussen.
    local zeile name wert cr
    cr=$'\r'
    while IFS= read -r zeile || [ -n "$zeile" ]; do
        case "$zeile" in
            LITELLM_DEV_MASTER_KEY=*|KAI_DEV_LITELLM_*=*)
                name="${zeile%%=*}"
                wert="${zeile#*=}"
                wert="${wert%"$cr"}"
                wert="${wert%\"}"; wert="${wert#\"}"
                wert="${wert%\'}"; wert="${wert#\'}"
                export "$name=$wert"
                ;;
        esac
    done < "$env_datei"

    local fehlend=()
    for name in LITELLM_DEV_MASTER_KEY \
                KAI_DEV_LITELLM_ECONOMY_MODEL KAI_DEV_LITELLM_ECONOMY_API_KEY \
                KAI_DEV_LITELLM_CODE_MODEL KAI_DEV_LITELLM_CODE_API_KEY \
                KAI_DEV_LITELLM_FRONTIER_MODEL KAI_DEV_LITELLM_FRONTIER_API_KEY; do
        [ -n "${!name:-}" ] || fehlend+=("$name")
    done
    [ "${#fehlend[@]}" -eq 0 ] || _ab "DEV_RESERVE_ENV_INCOMPLETE: ${fehlend[*]} (leer oder fehlt in $env_datei) -- kein Start"

    if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":$PORT "; then
        _ab "DEV_RESERVE_PORT_BUSY: $HOST:$PORT belegt -- laeuft der Dev-Proxy schon?"
    fi

    # Preise aus der Kostenmap des Transport-Baums statt von GitHub `main`
    # (wie kai-litellm.service); die Smoke-Kosten > 0 haengen daran.
    export LITELLM_LOCAL_MODEL_COST_MAP=True
    echo "DEV_RESERVE_PROXY_START host=$HOST port=$PORT config=$konfig repo=$repo" >&2
    exec "$exec_skript" litellm --config "$konfig" --host "$HOST" --port "$PORT"
}

cmd_smoke() {
    local basis="${KAI_DEV_RESERVE_URL:-http://$HOST:$PORT}"
    local schluessel="${KAI_DEV_LITELLM_KEY:-}"
    local py="${KAI_PY:-python}"
    local routen="$ROUTEN_STANDARD"
    [ "${1:-}" = "--include-frontier" ] && routen="$ROUTEN_ALLE"
    [ -n "$schluessel" ] || _ab "DEV_RESERVE_NO_KEY: KAI_DEV_LITELLM_KEY nicht gesetzt"

    local tmp; tmp="$(mktemp -d)"
    local rc=0 route
    for route in $routen; do
        local kopf="$tmp/$route.headers" rumpf="$tmp/$route.json" code
        code="$(curl -sS -o "$rumpf" -D "$kopf" -w '%{http_code}' \
            -H "Authorization: Bearer $schluessel" -H "Content-Type: application/json" \
            --max-time 120 \
            -d "{\"model\":\"$route\",\"max_tokens\":8,\"messages\":[{\"role\":\"user\",\"content\":\"Antworte nur mit: ok\"}]}" \
            "$basis/v1/chat/completions" 2>/dev/null || echo 000)"
        # Beurteilt wird pro Route, ohne Werte auszugeben: Statuscode, gemeldete
        # Modellidentitaet, Kostenkopf. Kosten 0 oder fehlend = FAIL_CLOSED,
        # denn der Transport setzt unbekannte Kosten still auf 0.
        "$py" - "$route" "$code" "$kopf" "$rumpf" <<'PY' || rc=1
import json, sys
route, code, kopf, rumpf = sys.argv[1:]
kosten = None
for zeile in open(kopf, encoding="utf-8", errors="replace"):
    if zeile.lower().startswith("x-litellm-response-cost:"):
        try:
            kosten = float(zeile.split(":", 1)[1].strip())
        except ValueError:
            kosten = None
modell = finish = None
try:
    d = json.load(open(rumpf, encoding="utf-8"))
    modell = d.get("model")
    finish = (d.get("choices") or [{}])[0].get("finish_reason")
except Exception:
    pass
gruende = []
if code != "200":
    gruende.append(f"http={code}")
if not modell:
    gruende.append("modell_unbekannt")
if kosten is None:
    gruende.append("kosten_fehlen")
elif kosten <= 0:
    gruende.append("kosten_null")
status = "PASS" if not gruende else "FAIL_CLOSED"
print(f"{status} route={route} http={code} model={modell} finish={finish} cost_usd={kosten} {' '.join(gruende)}")
sys.exit(0 if not gruende else 1)
PY
    done
    rm -rf "$tmp"
    if [ "$rc" -ne 0 ]; then
        echo "DEV_RESERVE_FAIL_CLOSED: mindestens eine Route ohne bewiesene Identitaet/Kosten -- nicht benutzen" >&2
        exit 1
    fi
    echo "DEV_RESERVE_SMOKE_PASS routes=[$routen]"
}

cmd_tunnel() {
    echo "ssh -N -L $PORT:$HOST:$PORT ubuntu@192.168.178.23"
    echo "# danach auf dem Laptop: export KAI_DEV_LITELLM_KEY=<LITELLM_DEV_MASTER_KEY der Pi>; bash scripts/dev_reserve.sh smoke"
}

case "${1:-}" in
    proxy)  shift; cmd_proxy "$@" ;;
    smoke)  shift; cmd_smoke "$@" ;;
    tunnel) cmd_tunnel ;;
    *) echo "Aufruf: $0 {proxy|smoke [--include-frontier]|tunnel}" >&2; exit 2 ;;
esac
