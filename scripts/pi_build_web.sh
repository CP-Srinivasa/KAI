#!/usr/bin/env bash
# Build the React/Vite SPA on the Pi.
#
# D-208 — replaces the manual web/dist scp from the laptop after cutover.
# Designed to be idempotent and called either:
#   - manually after `git pull` if web/ source changed
#   - automatically from scripts/pi_install_systemd.sh
#
# Usage:
#   bash scripts/pi_build_web.sh            # build if web/dist is stale
#   bash scripts/pi_build_web.sh --force    # build regardless
#   bash scripts/pi_build_web.sh --check    # only report staleness, exit 0/1
#
# Exit codes:
#   0  build succeeded OR dist already up to date
#   1  Node/npm missing
#   2  npm ci or npm run build failed
#   3  --check found dist stale (--check exit code only)

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/web" 2>/dev/null || {
    echo "ERROR: $ROOT/web does not exist" >&2
    exit 1
}

FORCE=0
CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --check) CHECK_ONLY=1 ;;
        -h|--help) sed -n '1,17p' "$0"; exit 0 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# Node + npm presence
if ! command -v node >/dev/null 2>&1; then
    echo "ERROR: node not found on PATH." >&2
    echo "  Install on Ubuntu: sudo apt-get install -y nodejs npm" >&2
    exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
    echo "ERROR: npm not found on PATH." >&2
    echo "  Install on Ubuntu: sudo apt-get install -y nodejs npm" >&2
    exit 1
fi

# Staleness probe: any source file newer than dist/index.html?
# If dist/index.html doesn't exist, build is required.
is_stale() {
    if [[ ! -f dist/index.html ]]; then
        return 0
    fi
    # find any tracked source file newer than the build output.
    # -newer compares mtime; first match short-circuits via -quit.
    local newer
    newer="$(find src public index.html package.json package-lock.json \
        vite.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json \
        tailwind.config.js postcss.config.js \
        -newer dist/index.html -print -quit 2>/dev/null)"
    [[ -n "$newer" ]]
}

if (( CHECK_ONLY == 1 )); then
    if is_stale; then
        echo "stale: web/dist needs rebuild"
        exit 3
    else
        echo "fresh: web/dist is up to date"
        exit 0
    fi
fi

if (( FORCE == 0 )) && ! is_stale; then
    echo "web/dist is up to date — skipping build (use --force to rebuild)"
    exit 0
fi

echo "=== node $(node --version) / npm $(npm --version) ==="

# Use `npm ci` if package-lock.json is present (deterministic install);
# else fall back to `npm install`.
if [[ -f package-lock.json ]]; then
    echo "=== npm ci ==="
    npm ci --silent || {
        echo "ERROR: npm ci failed" >&2
        exit 2
    }
else
    echo "=== npm install (no lock file) ==="
    npm install --silent || {
        echo "ERROR: npm install failed" >&2
        exit 2
    }
fi

echo "=== npm run build ==="
npm run build || {
    echo "ERROR: npm run build failed" >&2
    exit 2
}

if [[ ! -f dist/index.html ]]; then
    echo "ERROR: dist/index.html missing after build" >&2
    exit 2
fi

SIZE="$(du -sh dist 2>/dev/null | awk '{print $1}')"
COUNT="$(find dist -type f | wc -l)"
echo "build OK — dist/ contains $COUNT files, total $SIZE"
exit 0
