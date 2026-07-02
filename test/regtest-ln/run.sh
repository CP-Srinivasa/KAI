#!/usr/bin/env bash
# PR7 regtest E2E — one-command full L402 round-trip against a real (regtest) lnd.
# Spins up bitcoind + 2 lnd, funds a payer (bob), opens a channel (inbound liquidity for
# the payee alice), bakes an invoices:write macaroon, then drives KAI's OWN code:
# mint -> pay -> settle -> L402-verify -> book. Capital-free (regtest coins).
#
# Usage (from repo root):  bash test/regtest-ln/run.sh
#   KEEP=1  -> leave the stack up afterwards (default: tear down)
# On Git Bash / Windows, MSYS_NO_PATHCONV=1 is REQUIRED (container paths get mangled
# otherwise) — this script sets it.
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"

BTC="docker exec kai-rt-bitcoind bitcoin-cli -regtest -rpcuser=kai -rpcpassword=kai -rpcwallet=rt"
LA="docker exec kai-rt-alice lncli --lnddir=/home/lnd/.lnd -n regtest"
LB="docker exec kai-rt-bob lncli --lnddir=/home/lnd/.lnd -n regtest"
_pk() { python -c "import sys,json;print(json.load(sys.stdin)['identity_pubkey'])"; }

echo "== up =="
docker compose up -d
docker exec kai-rt-bitcoind bitcoin-cli -regtest -rpcuser=kai -rpcpassword=kai createwallet rt >/dev/null 2>&1 || true

echo "== advance chain so lnd syncs (110 blocks to a bitcoind addr) =="
BADDR=$($BTC getnewaddress)
$BTC generatetoaddress 110 "$BADDR" >/dev/null

echo "== wait for both lnd synced_to_chain =="
for n in alice bob; do
  for i in $(seq 1 30); do
    L="docker exec kai-rt-$n lncli --lnddir=/home/lnd/.lnd -n regtest"
    if [ "$($L getinfo 2>/dev/null | python -c 'import sys,json;print(json.load(sys.stdin)["synced_to_chain"])' 2>/dev/null)" = "True" ]; then break; fi
    sleep 4
  done
done

echo "== fund bob (payer) =="
BOBADDR=$($LB newaddress p2wkh | python -c 'import sys,json;print(json.load(sys.stdin)["address"])')
$BTC sendtoaddress "$BOBADDR" 5 >/dev/null
$BTC generatetoaddress 6 "$($BTC getnewaddress)" >/dev/null
sleep 3

echo "== open channel bob->alice (inbound liquidity for alice) =="
APK=$($LA getinfo | _pk)
$LB connect "${APK}@alice:9735" 2>/dev/null || true
$LB openchannel --node_key="$APK" --local_amt=1000000 --sat_per_vbyte=1 >/dev/null
$BTC generatetoaddress 6 "$($BTC getnewaddress)" >/dev/null
sleep 4
echo "   alice inbound = $($LA channelbalance | python -c 'import sys,json;print(json.load(sys.stdin)["remote_balance"]["sat"])') sat"

echo "== bake invoices:write macaroon on alice + drive the round-trip =="
MAC=$($LA bakemacaroon invoices:write invoices:read | tr -d '\r\n ')
( cd "$ROOT" && PYTHONPATH=. python test/regtest-ln/driver.py "$MAC" )

if [ "${KEEP:-0}" != "1" ]; then echo "== teardown =="; docker compose -p kai-regtest-ln down -v >/dev/null 2>&1 || docker compose down -v; fi
