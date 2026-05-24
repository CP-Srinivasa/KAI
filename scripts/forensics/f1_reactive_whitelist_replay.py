#!/usr/bin/env python3
"""F1 Replay: alle 14d reactive_price_narrative blocks gegen neue Whitelist.

Reproducible from repo root:
    python scripts/forensics/f1_reactive_whitelist_replay.py

Used for sign-off of `_has_substantive_trigger`-Whitelist 2026-05-24.
14d-Sample period: 2026-05-10..24, all blocked_alerts entries with
block_reason="reactive_price_narrative". Target: Recovery >= 80%, FP-Rate 0%.
"""
from app.alerts.eligibility import _has_substantive_trigger

# Aus blocked_alerts.jsonl Pi 14d 2026-05-10..24, reason=reactive_price_narrative.
HEADLINES = [
    ("morgan stanleys msbt ends first trading month with 0 outflows amid bitcoin etfs 6 week inflow streak", "expect_pass"),
    ("xrp spikes 2 5 beating bitcoin and ether in breakout above 1 45", "expect_block"),
    ("sui spikes 50 amid staking moves zero fee stablecoins privacy push", "expect_pass"),
    ("blackrock bets on circle as 222 million arc raise ignites crcl stock surge", "expect_pass"),
    ("bitcoin surges above 82 000 amid us iran de escalation signals", "expect_pass"),
    ("solana etf inflows show demand returning as traders eye sol rally to 120", "expect_pass"),
    ("chainlink network activity surges to 8 month high as ccip wins defi migration", "expect_pass"),
    ("the real reason zcash zec is pumping", "expect_block"),
    ("buildon b explodes 55 in 24 hours is 0 74 the next stop", "expect_block"),
    ("xrp leans on institutional flows for 12 price breakout push", "borderline"),
    ("circle stock explodes as long stalled clarity act passes senate vote", "expect_pass"),
    ("crypto rallies as senate committee advances market structure bill to full senate", "expect_pass"),
    ("bitwise hyperliquid etf to start trading friday as hype rallies on coinbase usdc deal", "expect_pass"),
    ("hype jumps as coinbase and circle back hyperliquids stablecoin model", "expect_pass"),
    ("the truth behind the ton pump", "expect_block"),
    ("sharplink ceo points out 3 catalysts for ethereum s price to surge higher", "borderline"),
    ("hyperliquid s hype token rallies 7 as trade xyz launches first pre ipo perpetual market for spacex", "expect_pass"),
    ("ondo breaks out of 3 month accumulation zone jumps 16 in a day", "expect_block"),
    ("3 catalysts powering lighters 20 rally today", "borderline"),
    ("bitcoin surges past 78 000 triggers 30m in short liquidations", "expect_block"),
    ("near protocol to automate growth with dynamic resharding upgrade in june near token surges 27", "expect_pass"),
    ("near protocol leads ai token rally with a 50 pump is 5 near price next", "borderline"),
    ("near protocol surges 30 after arthur hayes calls it part of holy trinity", "borderline"),
    ("sui mainnet to introduce private transactions token surges over 20", "expect_pass"),
    ("iran and us move closer to finalizing memorandum of understanding as bitcoin surges past 82k", "expect_pass"),
    ("iran and us near memorandum of understanding as bitcoin rallies past 82k on de escalation hopes", "expect_pass"),
]

stats = {"expect_pass_OK": 0, "expect_pass_FAIL": 0, "expect_block_OK": 0,
         "expect_block_FAIL": 0, "borderline_pass": 0, "borderline_block": 0}

print(f"{'title':100s} {'expect':15s} {'pred':6s} {'result'}")
print("=" * 140)
for title, expect in HEADLINES:
    will_pass = _has_substantive_trigger(title)
    pred = "PASS" if will_pass else "BLOCK"

    if expect == "expect_pass":
        ok = will_pass
        result = "OK" if ok else "MISS"
        stats[f"expect_pass_{'OK' if ok else 'FAIL'}"] += 1
    elif expect == "expect_block":
        ok = not will_pass
        result = "OK" if ok else "FP"  # false positive = unwanted pass
        stats[f"expect_block_{'OK' if ok else 'FAIL'}"] += 1
    else:  # borderline
        result = "OK"
        stats[f"borderline_{'pass' if will_pass else 'block'}"] += 1

    print(f"{title[:100]:100s} {expect:15s} {pred:6s} {result}")

print()
print("=== Summary ===")
for k, v in stats.items():
    print(f"  {k}: {v}")
print()
recovery_rate = stats["expect_pass_OK"] / (stats["expect_pass_OK"] + stats["expect_pass_FAIL"]) * 100 if (stats["expect_pass_OK"] + stats["expect_pass_FAIL"]) else 0
fp_rate = stats["expect_block_FAIL"] / (stats["expect_block_OK"] + stats["expect_block_FAIL"]) * 100 if (stats["expect_block_OK"] + stats["expect_block_FAIL"]) else 0
print(f"Recovery-Rate (gewuenschte Pass): {recovery_rate:.0f}%")
print(f"False-Positive-Rate (unerwuenschte Pass): {fp_rate:.0f}%")
