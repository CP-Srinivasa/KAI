# Runbook: Wertneutraler Inbound-Swap (Boltz Reverse, G3-S2)

**Stand:** 2026-07-02 · **Bezug:** Goal-Plan G3 / ADR 0013 · **Voraussetzungen (ERFÜLLT 07-02):** `boltzd` v2.12.1 auf .51 (checksum-verifiziert, `boltzd.service` aktiv, mit lnd + Boltz-wss verbunden).

## Zweck

LN-Guthaben → eigenes on-chain-Wallet zurück (Self-Custody-Roundtrip): nur Fees gehen weg, der Channel gewinnt Inbound-Kapazität in Swap-Höhe. Kein Dritter hält je Wert (HTLC-atomar; Claim macht boltzd automatisch — genau dafür wurde er installiert).

## Gates (VOR Ausführung prüfen)

1. **Operator-Betrag-Ok** liegt vor (extern/irreversibel: Fees ~0,25–0,5 % + Claim-Mining-Fee).
2. **Tages-Cap-Fenster:** `trading`-Policy zählt UTC-Tag; `spent_today_sat()` + Swap-Betrag + Fee-Reserve ≤ `daily_cap_sat` (aktuell 30.000).
3. **Reserve-Floor:** Gesamt-Balance − Swap-Fees bleibt > `reserve_floor_sat` (Floor wird nie berührt — der Hauptbetrag kommt on-chain zurück).
4. Boltz-Limits/Fees aktuell prüfen: `sudo -u bitcoin boltzcli getpairs 2>/dev/null | grep -A6 BTC` (Mindestbetrag beachten).

## Ausführung (auf .51, als admin)

```bash
# 1. Ziel-Adresse aus dem EIGENEN lnd-Wallet (Self-Custody-Beweis):
ADDR=$(sudo -u bitcoin lncli newaddress p2tr | python3 -c 'import json,sys; print(json.load(sys.stdin)["address"])')
echo "$ADDR"

# 2. Reverse-Swap (Betrag in sat, vom Operator benannt, z. B. 25000):
sudo -u bitcoin boltzcli createreverseswap BTC 25000 "$ADDR"

# 3. Fortschritt (boltzd claimt automatisch nach Lockup-Bestätigung):
sudo -u bitcoin boltzcli swapinfo <swap-id>
sudo journalctl -u boltzd -f --no-pager   # bis "claim transaction broadcast"
```

⚠ **Check-vor-Retry** (Lehre Channel-Open 07-01): Bei Client-Timeout/„error" NIE blind wiederholen — erst `swapinfo`/`listswaps` + `lncli listpayments` prüfen; die Zahlung kann settled sein.

## Nachlauf (auf dem KAI-Pi, Pflicht)

```bash
# Balancen-Beweis (local ↓ um Swap, remote/Inbound ↑, on-chain ↑ nach Claim-Conf):
ssh admin@192.168.178.51 'sudo -u bitcoin lncli listchannels | grep -E "local_balance|remote_balance"; sudo -u bitcoin lncli walletbalance | grep confirmed'

# Provenienz + Attestation (Transfer self→self, Lockup-Txid aus swapinfo):
cd /home/ubuntu/ai_analyst_trading_bot
./.venv/bin/trading-bot trading provenance-record --kind transfer --wallet 024a7f9cfaa7d9d00b5a4a70d756a91d68287012074e914de3fbd49ffd03fd0b44 --tx-hash <claim-txid> --counterparty "Boltz (Reverse-Swap, self-custody-Roundtrip)" --amount <betrag> --currency sat --note "G3-S2 Inbound-Swap: LN->eigenes on-chain, Fees <x> sat"
./.venv/bin/trading-bot trading compliance-export --out artifacts/compliance/compliance_export_$(date +%Y%m%d).json
./.venv/bin/trading-bot trading truth-attest-file artifacts/compliance/compliance_export_$(date +%Y%m%d).json --kind compliance_export
./.venv/bin/trading-bot trading truth-verify
```

**Hinweis Governance:** boltzd zahlt die Hold-Invoice direkt über lnd (Admin-Macaroon) — er läuft AN der App-Policy vorbei. Deshalb ist dieses Runbook selbst das Gate: Operator-Betrag-Ok + Cap-/Floor-Check oben sind Pflicht, und der Nachlauf bucht den Vorgang in dieselben Ledger wie App-Spends. Falls Swaps regelmäßig werden: App-seitige Kapselung (value_layer-Aktion `reverse_swap`) als Folgearbeit.

**Done-Kriterium (G3):** Inbound ≥ 50k · Sats zurück im eigenen Wallet · Transfer im Provenienz-Ledger + attestiert · `truth-verify` grün.
