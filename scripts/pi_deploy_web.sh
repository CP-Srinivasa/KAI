#!/usr/bin/env bash
# Build the Vite SPA on the laptop and transfer dist/ to the Pi.
#
# D-208 — replaces the never-finished pi_build_web.sh on-Pi attempt.
# The Pi-4b 1GB variant cannot run `npm ci + tsc + vite build` reliably
# (OOM under memory pressure, SSH banner timeouts). Doing the build on
# the laptop is faster (<30s) and avoids Pi service-disruption.
#
# Usage:
#   bash scripts/pi_deploy_web.sh ubuntu@192.168.178.23
#   bash scripts/pi_deploy_web.sh ubuntu@192.168.178.23 --skip-build  # use existing dist
#   bash scripts/pi_deploy_web.sh --check ubuntu@192.168.178.23       # only verify staleness
#
# Idempotent: if web/dist is fresh and Pi has matching dist, no transfer.
# Verifies via sha256 of tarball after extract.
#
# Exit codes:
#   0  deployed (or already up-to-date with --skip-build)
#   1  build failed
#   2  transfer or verify failed (auch: BUNDLE_MISMATCH -- der Server
#      liefert nicht das uebertragene Bundle aus)
#   3  SPA_TRANSFERRED_NOT_ACTIVATED -- ein Release regiert; die SPA liegt
#      im Checkout, wird aber erst mit einem neuen Release ausgeliefert

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SKIP_BUILD=0
CHECK_ONLY=0
REMOTE_HOST=""
# Prod-Pi-Repo-Pfad. Korrektur 2026-06-16: die Prod-Node läuft als User `ubuntu`
# unter /home/ubuntu/... — der alte /home/kai-Default war falsch und liess den
# remote `cd` (set -e) scheitern. Per Env überschreibbar, falls die Umgebung driftet.
REMOTE_ROOT="${REMOTE_ROOT:-/home/ubuntu/ai_analyst_trading_bot}"

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
    echo "ERROR: remote host required (e.g. ubuntu@192.168.178.23)" >&2
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

echo "=== extract on $REMOTE_HOST (atomic-swap) ==="
# Atomic-swap-Deploy:
# 1. Extract in staging-dir (NICHT in web/dist direkt — sonst tar merged
#    ueber alte Vite-Hash-Bundles und sammelt Disk-Muell).
# 2. Rotate alte web/dist als .dist-prev-deploy-<timestamp> (Rollback-Anker).
# 3. mv staging -> web/dist (atomic-ish — FastAPI StaticFiles sieht nur
#    den Endzustand nach dem mv).
# 4. Cleanup .dist-prev-deploy-* aelter als 3 Tage.
#
# Memory feedback_pi_deploy_web_dist_no_clean dokumentiert den alten Bug:
# tar -xzf web/dist akkumuliert ~5MB Muell pro 10 Deploys.
STAGE_TS="$(date -u +%Y%m%dT%H%M%SZ)"
ssh -o BatchMode=yes "$REMOTE_HOST" "
    set -e
    cd '$REMOTE_ROOT'
    rm -rf web/dist.deploy-staging
    mkdir -p web/dist.deploy-staging
    # Tarball-Pfad ist 'web/dist/...' (gepackt mit -C \$ROOT web/dist).
    # --strip-components=1 entfernt das 'web/'-prefix, landet bei
    # web/dist.deploy-staging/dist/...
    tar -xzf /tmp/kai_web_dist.tar.gz -C web/dist.deploy-staging --strip-components=1
    if [ ! -f web/dist.deploy-staging/dist/index.html ]; then
        echo 'ERROR: staging dist missing index.html — extract failed' >&2
        rm -rf web/dist.deploy-staging
        exit 2
    fi
    # Rotate alte dist als rollback-Anker (nur wenn vorhanden).
    if [ -d web/dist ]; then
        mv web/dist 'web/.dist-prev-deploy-${STAGE_TS}'
    fi
    mv web/dist.deploy-staging/dist web/dist
    rmdir web/dist.deploy-staging
    rm -f /tmp/kai_web_dist.tar.gz
    # Cleanup rotate-anchors aelter als 3 Tage (idempotent, schweigt wenn keine).
    find web -maxdepth 1 -name '.dist-prev-deploy-*' -type d -mtime +3 -exec rm -rf {} + 2>/dev/null || true
    echo \"  remote dist: \$(find web/dist -type f | wc -l) files, \$(du -sh web/dist | awk '{print \$1}')\"
    echo \"  rollback-anchors: \$(ls -d web/.dist-prev-deploy-* 2>/dev/null | wc -l)\"
" || { echo "ERROR: remote extract failed" >&2; exit 2; }

# Welches Bundle SOLL nach dem Transfer ausgeliefert werden? Der Name kommt aus
# dem gerade extrahierten `index.html` -- nicht aus einer Annahme hier.
ERWARTET="$(ssh -o BatchMode=yes "$REMOTE_HOST" \
    "grep -oE 'assets/index-[A-Za-z0-9_-]+\.js' '$REMOTE_ROOT/web/dist/index.html' | head -1" \
    2>/dev/null || true)"
[ -n "$ERWARTET" ] || { echo "ERROR: kein Bundle-Name im uebertragenen index.html" >&2; exit 2; }
echo "  erwartetes Bundle: $ERWARTET"

# REGIERT EIN RELEASE? Dann laedt kai-server die SPA aus `current`, nicht aus dem
# Checkout -- ein Restart hier ist folgenlos.
#
# Gemessen 2026-09-04: dieses Skript hat `web/dist` im Checkout aktualisiert,
# kai-server neu gestartet und "Deploy complete. /dashboard/ HTTP 200 -- SPA
# serving" gemeldet, waehrend weiterhin das ZWEI WOCHEN alte Bundle ausgeliefert
# wurde. Der Statuscode sagt nichts ueber den Inhalt.
RELEASES_DIR="$(dirname "$REMOTE_ROOT")"
RELEASE="$(ssh -o BatchMode=yes "$REMOTE_HOST" \
    "readlink -f '$RELEASES_DIR/current' 2>/dev/null || true" 2>/dev/null || true)"

if [ -n "$RELEASE" ]; then
    {
        echo "=== Release-Modell aktiv: $RELEASE ==="
        echo "Die SPA liegt jetzt im Checkout, ausgeliefert wird aber aus dem Release."
        echo "Ein kai-server-Restart aendert daran nichts -- deshalb wird hier keiner"
        echo "ausgeloest und kein Erfolg gemeldet."
        echo
        echo "Naechste Schritte auf $REMOTE_HOST (unprivilegiert):"
        echo "  cd $REMOTE_ROOT"
        echo "  bash scripts/pi_make_release.sh --repo $REMOTE_ROOT \\"
        echo "       --releases $RELEASES_DIR/releases --state $REMOTE_ROOT --rebuild"
        echo "  bash scripts/pi_activate_release.sh --release <neuer Pfad> \\"
        echo "       --current $RELEASES_DIR/current --state $REMOTE_ROOT"
        echo "  for u in kai-server kai-agent-worker kai-tg-listener kai-entry-watch \\"
        echo "           kai-liquidation-stream; do"
        echo "    sudo -n /usr/local/sbin/kai-service-control restart \$u.service; done"
        echo
        echo "SPA_TRANSFERRED_NOT_ACTIVATED"
    } >&2
    exit 3
fi

echo "=== restart kai-server (load new dist via StaticFiles mount) ==="
ssh -o BatchMode=yes "$REMOTE_HOST" \
    "sudo -n /usr/local/sbin/kai-service-control restart kai-server.service" || {
    echo "WARNING: kai-server restart failed -- invoke manually:" >&2
    echo "  ssh $REMOTE_HOST 'sudo -n /usr/local/sbin/kai-service-control restart kai-server.service'" >&2
    exit 2
}

# Der Smoke prueft den INHALT, nicht den Statuscode. Ein 200 beweist, dass
# irgendeine SPA ausgeliefert wird -- nicht, dass es die gerade uebertragene ist.
#
# Die REMOTE-Seite holt nur Daten, entschieden wird HIER. Eine Entscheidung im
# fernen Shell-String ist weder testbar noch lesbar: sie liegt hinter zwei
# Ebenen Quoting und laesst sich nur mit einem echten Pi pruefen.
echo "=== smoke: /dashboard/ liefert $ERWARTET ? ==="
ssh -o BatchMode=yes "$REMOTE_HOST" \
    "until curl -s --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1; do sleep 1; done" \
    || { echo "  /health antwortet nicht" >&2; exit 2; }

CODE="$(ssh -o BatchMode=yes "$REMOTE_HOST" \
    "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/dashboard/" 2>/dev/null || true)"
if [ "$CODE" != "200" ]; then
    echo "  /dashboard/ HTTP $CODE -- NOT 200, check logs" >&2
    exit 2
fi

SERVIERT="$(ssh -o BatchMode=yes "$REMOTE_HOST" \
    "curl -s --max-time 5 http://127.0.0.1:8000/dashboard/ | grep -oE 'assets/index-[A-Za-z0-9_-]+\.js' | head -1" \
    2>/dev/null || true)"
if [ "$SERVIERT" != "$ERWARTET" ]; then
    echo "  BUNDLE_MISMATCH: ausgeliefert ${SERVIERT:-<keins>}, erwartet $ERWARTET" >&2
    echo "  Der Transfer ist angekommen, der Server liefert ihn nicht aus." >&2
    exit 2
fi
echo "  /dashboard/ HTTP 200, Bundle $SERVIERT -- bestaetigt"

echo "Deploy complete."
exit 0
