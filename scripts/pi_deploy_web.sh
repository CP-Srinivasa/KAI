#!/usr/bin/env bash
# Build the Vite SPA on the laptop and transfer dist/ to the Pi.
#
# D-208 — replaces the never-finished pi_build_web.sh on-Pi attempt.
# The Pi-4b 1GB variant cannot run `npm ci + tsc + vite build` reliably
# (OOM under memory pressure, SSH banner timeouts). Doing the build on
# the laptop is faster (<30s) and avoids Pi service-disruption.
#
# Usage:
#   bash scripts/pi_deploy_web.sh ubuntu@192.168.178.20
#   bash scripts/pi_deploy_web.sh ubuntu@192.168.178.20 --skip-build  # use existing dist
#   bash scripts/pi_deploy_web.sh --check ubuntu@192.168.178.20       # only verify staleness
#
# Idempotent: if web/dist is fresh and Pi has matching dist, no transfer.
# Verifies via sha256 of tarball after extract.
#
# Exit codes:
#   0  deployed (or already up-to-date with --skip-build)
#   1  build failed
#   2  transfer or verify failed

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SKIP_BUILD=0
CHECK_ONLY=0
REMOTE_HOST=""
REMOTE_ROOT="/home/kai/ai_analyst_trading_bot"

for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=1 ;;
        --check) CHECK_ONLY=1 ;;
        -h|--help) sed -n '1,17p' "$0"; exit 0 ;;
        -*) echo "unknown flag: $arg" >&2; exit 2 ;;
        *)
            if [[ -z "$REMOTE_HOST" ]]; then
                REMOTE_HOST="$arg"
            else
                echo "unexpected positional: $arg" >&2; exit 2
            fi
            ;;
    esac
done

if [[ -z "$REMOTE_HOST" ]]; then
    echo "ERROR: remote host required (e.g. ubuntu@192.168.178.20)" >&2
    sed -n '8,15p' "$0" >&2
    exit 2
fi

# 1. Pre-flight ssh probe (fail-fast, BEFORE building)
echo "=== ssh probe: $REMOTE_HOST ==="
if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$REMOTE_HOST" "echo ok" >/dev/null 2>&1; then
    echo "ERROR: ssh probe to $REMOTE_HOST failed (key auth, BatchMode=yes)" >&2
    exit 2
fi

# 2. Build (unless --skip-build)
if (( SKIP_BUILD == 0 )); then
    echo "=== local build: web/ ==="
    if ! command -v npm >/dev/null 2>&1; then
        echo "ERROR: npm not on PATH (laptop needs node + npm to build)" >&2
        exit 1
    fi
    cd web
    npm run build || { echo "ERROR: npm run build failed" >&2; exit 1; }
    cd "$ROOT"
fi

if [[ ! -f web/dist/index.html ]]; then
    echo "ERROR: web/dist/index.html missing after build" >&2
    exit 1
fi

LOCAL_FILES="$(find web/dist -type f | wc -l)"
LOCAL_SIZE="$(du -sh web/dist | awk '{print $1}')"
echo "=== local dist: $LOCAL_FILES files, $LOCAL_SIZE ==="

if (( CHECK_ONLY == 1 )); then
    echo "(check mode — skipping transfer)"
    exit 0
fi

# 3. Tar + scp + extract
TARBALL="$(mktemp -t kai_web_dist.XXXXXX.tar.gz)"
trap 'rm -f "$TARBALL"' EXIT

echo "=== packing $TARBALL ==="
tar -czf "$TARBALL" -C "$ROOT" web/dist || { echo "ERROR: tar failed" >&2; exit 2; }
LOCAL_SHA="$(sha256sum "$TARBALL" | awk '{print $1}')"

echo "=== transfer to $REMOTE_HOST ==="
if ! scp -o BatchMode=yes "$TARBALL" "$REMOTE_HOST:/tmp/kai_web_dist.tar.gz"; then
    echo "ERROR: scp failed" >&2
    exit 2
fi

REMOTE_SHA="$(ssh -o BatchMode=yes "$REMOTE_HOST" "sha256sum /tmp/kai_web_dist.tar.gz | awk '{print \$1}'")"
if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
    echo "ERROR: sha256 mismatch after scp ($LOCAL_SHA vs $REMOTE_SHA)" >&2
    exit 2
fi
echo "  sha256 match: ${LOCAL_SHA:0:16}..."

echo "=== extract on $REMOTE_HOST ==="
ssh -o BatchMode=yes "$REMOTE_HOST" "
    set -e
    cd '$REMOTE_ROOT'
    tar -xzf /tmp/kai_web_dist.tar.gz
    rm -f /tmp/kai_web_dist.tar.gz
    echo \"  remote dist: \$(find web/dist -type f | wc -l) files, \$(du -sh web/dist | awk '{print \$1}')\"
" || { echo "ERROR: remote extract failed" >&2; exit 2; }

echo "=== restart kai-server (load new dist via StaticFiles mount) ==="
ssh -o BatchMode=yes "$REMOTE_HOST" "sudo systemctl restart kai-server" || {
    echo "WARNING: kai-server restart failed — invoke manually:" >&2
    echo "  ssh $REMOTE_HOST 'sudo systemctl restart kai-server'" >&2
    exit 2
}

echo "=== smoke: /dashboard/ on $REMOTE_HOST ==="
ssh -o BatchMode=yes "$REMOTE_HOST" "
    until curl -s --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1; do sleep 1; done
    code=\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/dashboard/)
    if [[ \"\$code\" == \"200\" ]]; then
        echo \"  /dashboard/ HTTP \$code — SPA serving\"
    else
        echo \"  /dashboard/ HTTP \$code — NOT 200, check logs\" >&2
        exit 2
    fi
" || exit 2

echo "Deploy complete."
exit 0
