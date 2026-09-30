#!/usr/bin/env bash
# macrot_pi.sh -- neue KAI-Macaroons auf der Pi einsetzen und beweisen (Macaroon-Neuaufbau 29.09.2026).
#
# KAI liest die Macaroon-Dateien bei JEDEM lnd-Aufruf neu (app/lightning/adapter.py
# _build_client -> client._load_macaroon_hex). Deshalb: gleiche Dateinamen, Inhalt
# atomar tauschen -- keine .env-Aenderung, kein Dienst-Neustart.
#
#   bash ~/kai-macrot/macrot_pi.sh install    ~/kai-secrets/lnd/incoming/* pruefen und einsetzen
#   bash ~/kai-macrot/macrot_pi.sh verify     neue Schluessel gehen, alte werden abgelehnt, Preflight GO
#   bash ~/kai-macrot/macrot_pi.sh rollback   *.pre-rot zuruecklegen
#   bash ~/kai-macrot/macrot_pi.sh cleanup    *.pre-rot vernichten
set -uo pipefail
umask 077

S=/home/ubuntu/kai-secrets/lnd
IN=$S/incoming
H=/home/ubuntu/kai-macrot
REPO=/home/ubuntu/ai_analyst_trading_bot
# neu (vom Node) : Ziel (Name, den KAI per .env liest)
MAP="kai-readonly.macaroon:readonly.macaroon kai-invoice.macaroon:kai-invoice.macaroon kai-account.macaroon:kai-account.macaroon"

say() { echo "== $(date -u +%H:%M:%SZ) $*"; }
die() { echo "ABBRUCH: $*" >&2; exit 1; }

drain_ok() {
    (cd "$REPO" && ./.venv/bin/python -m scripts.payment_drain_check \
        --journal artifacts/payments/payment_journal.jsonl) || die "Zahlung unterwegs oder Journal unlesbar"
}

install_keys() {
    [ -d "$IN" ] || die "$IN fehlt (erst Transfer vom Node)"
    (cd "$IN" && sha256sum --quiet -c SHA256SUMS) || die "Pruefsummen passen nicht zum Node"
    python3 "$H/mac_ops.py" --expect 101 \
        "address:read info:read invoices:read macaroon:read message:read offchain:read onchain:read peers:read signer:read" \
        "$IN/kai-readonly.macaroon" || die "kai-readonly unerwartet"
    python3 "$H/mac_ops.py" --expect 102 "info:read invoices:read,write offchain:read onchain:read" \
        "$IN/kai-invoice.macaroon" || die "kai-invoice unerwartet"
    python3 "$H/mac_ops.py" --expect account "info:read invoices:read,write offchain:read,write onchain:read peers:read" \
        "$IN/kai-account.macaroon" || die "kai-account unerwartet"
    drain_ok
    local pair new dst
    for pair in $MAP; do
        new=${pair%%:*} dst=${pair##*:}
        [ -e "$S/$dst.pre-rot" ] && die "$S/$dst.pre-rot existiert schon (frueherer Lauf)"
    done
    for pair in $MAP; do
        new=${pair%%:*} dst=${pair##*:}
        cp -p "$S/$dst" "$S/$dst.pre-rot" || die "Sicherung $dst"
        install -m 600 "$IN/$new" "$S/$dst.tmp" || die "Kopie $new"
        mv -f "$S/$dst.tmp" "$S/$dst" || die "Tausch $dst"
        say "eingesetzt: $dst  (alt -> $dst.pre-rot)"
    done
    find "$IN" -type f -exec shred -u {} + && rmdir "$IN"
    say "incoming vernichtet"
}

probe() {
    (cd "$REPO" && ./.venv/bin/python - "$S") <<'PY'
import json, ssl, sys, urllib.error, urllib.request

s = sys.argv[1]
env = {}
for line in open(".env", encoding="utf-8"):
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.rstrip("\n").split("=", 1)
        env[k.strip()] = v.strip().strip("\"'")
base = f"https://{env['APP_LN_HOST']}:{env.get('APP_LN_REST_PORT', '8080')}"
ctx = ssl.create_default_context(cafile=env["APP_LN_TLS_CERT_PATH"])
ctx.check_hostname = False  # Kette wird gegen das gepinnte tls.cert geprueft


def call(path, mac):
    req = urllib.request.Request(base + path, headers={"Grpc-Metadata-macaroon": open(mac, "rb").read().hex()})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200].decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)[:200]


bad = 0
for name, path, want_ok in (
    ("readonly.macaroon (neu)", "/v1/getinfo", True),
    ("kai-invoice.macaroon (neu)", "/v1/getinfo", True),
    ("kai-account.macaroon (neu)", "/v1/balance/channels", True),
    ("readonly.macaroon.pre-rot (alt)", "/v1/getinfo", False),
    ("kai-invoice.macaroon.pre-rot (alt)", "/v1/getinfo", False),
    ("kai-account.macaroon.pre-rot (alt)", "/v1/balance/channels", False),
):
    fname = name.split(" ")[0]
    try:
        open(f"{s}/{fname}", "rb").close()
    except OSError:
        print(f"  --    {name}: Datei fehlt")
        continue
    code, body = call(path, f"{s}/{fname}")
    ok = code == 200
    extra = ""
    if ok and path == "/v1/balance/channels":
        extra = f" -> sieht {body.get('local_balance', {}).get('sat')} sat (virtuelles Budget, nicht Kanalguthaben)"
    elif not ok:
        extra = f" -> {str(body)[:110]}"
    good = ok == want_ok
    bad += not good
    print(f"  {'OK   ' if good else 'FEHLER'} {name}: HTTP {code}{extra}")
sys.exit(1 if bad else 0)
PY
}

verify() {
    say "Neue Schluessel muessen gehen, alte muessen abgelehnt werden"
    probe || die "Beweis nicht bestanden"
    say "Go-live-Preflight (nur lesend)"
    (cd "$REPO" && ./.venv/bin/python scripts/ln_golive_preflight.py) | tail -15
    [ "${PIPESTATUS[0]}" = 0 ] || die "Preflight nicht GO"
    say "KAI laeuft mit den neuen Schluesseln"
}

rollback() {
    local pair dst n=0
    for pair in $MAP; do
        dst=${pair##*:}
        if [ -e "$S/$dst.pre-rot" ]; then
            mv -f "$S/$dst.pre-rot" "$S/$dst" && n=$((n + 1))
        fi
    done
    say "$n Datei(en) zurueckgelegt"
}

cleanup() {
    local pair dst
    for pair in $MAP; do
        dst=${pair##*:}
        [ -e "$S/$dst.pre-rot" ] && shred -u "$S/$dst.pre-rot"
    done
    say "alte Schluessel auf der Pi vernichtet"
}

case "${1:-}" in
    install) install_keys ;;
    verify) verify ;;
    rollback) rollback ;;
    cleanup) cleanup ;;
    *) sed -n '2,13p' "$0"; exit 64 ;;
esac
