#!/usr/bin/env bash
# Read-only LiteLLM transport smoke test.  It never calls a model endpoint.
set -uo pipefail

BASE_URL="${KAI_LITELLM_SMOKE_BASE_URL:-http://127.0.0.1:4000}"
TIMEOUT_S="${KAI_LITELLM_SMOKE_TIMEOUT_S:-5}"
CURL_BIN="${KAI_LITELLM_SMOKE_CURL:-curl}"

while [ $# -gt 0 ]; do
    case "$1" in
        --base-url) BASE_URL="$2"; shift 2 ;;
        --timeout) TIMEOUT_S="$2"; shift 2 ;;
        *) echo "unbekanntes Argument: $1" >&2; exit 1 ;;
    esac
done

BASE_URL="${BASE_URL%/}"

probe() {
    local name="$1"
    local expected="$2"
    local path="$3"
    shift 3
    local status

    status="$("$CURL_BIN" --silent --show-error --output /dev/null --write-out '%{http_code}' \
        --connect-timeout "$TIMEOUT_S" --max-time "$TIMEOUT_S" \
        "$@" "$BASE_URL$path")" || {
        echo "FAIL $name transport_unreachable" >&2
        return 1
    }
    if [ "$status" != "$expected" ]; then
        echo "FAIL $name expected=$expected actual=$status" >&2
        return 1
    fi
    echo "PASS $name http=$status"
}

failed=0
probe "liveliness" "200" "/health/liveliness" || failed=1
probe "models_without_key" "401" "/v1/models" || failed=1
probe "models_with_wrong_key" "401" "/v1/models" \
    --header "Authorization: Bearer kai-intentionally-invalid-smoke-key" || failed=1

exit "$failed"
