"""D2-R4 Regime-Filter Cross-Tab: precision per regime klasse (BTC + ETH).

Joint alert_outcomes -> canonical_documents (via document_id) -> regime_state (via asset+timestamp).
Cross-Tab precision per regime, identifies regimes with significantly worse precision.

Run on Pi: python3 /tmp/d2_r4_analyze.py
"""
import json
import sqlite3
import datetime
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path.home() / "ai_analyst_trading_bot"
DB = REPO / "data" / "dev.db"
OUTCOMES = REPO / "artifacts" / "alert_outcomes.jsonl"
REGIME_BTC = REPO / "artifacts" / "regime_state" / "btc_regime.jsonl"
REGIME_ETH = REPO / "artifacts" / "regime_state" / "eth_regime.jsonl"

# Window: only post-R1-Observer-start (2026-05-09)
WINDOW_START = datetime.datetime(2026, 5, 9, tzinfo=datetime.UTC)


def load_regime(path, asset):
    """Returns sorted list of (datetime_utc, regime, vol_class)."""
    rows = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
            ts = datetime.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
            rows.append((ts, r["regime"], r["vol_class"]))
        except Exception:
            pass
    rows.sort(key=lambda x: x[0])
    return rows


def regime_lookup(regime_rows, target_ts, tolerance_hours=1.5):
    """Binary search closest regime within tolerance; return (regime, vol_class) or (None, None)."""
    if not regime_rows:
        return None, None
    lo, hi = 0, len(regime_rows) - 1
    best = None
    best_dt = datetime.timedelta(hours=999)
    while lo <= hi:
        mid = (lo + hi) // 2
        dt = abs(regime_rows[mid][0] - target_ts)
        if dt < best_dt:
            best_dt = dt
            best = regime_rows[mid]
        if regime_rows[mid][0] < target_ts:
            lo = mid + 1
        else:
            hi = mid - 1
    if best_dt > datetime.timedelta(hours=tolerance_hours):
        return None, None
    return best[1], best[2]


def wilson_lower(hits, n, z=1.96):
    if n == 0:
        return 0.0
    p = hits / n
    denom = 1 + z**2 / n
    num = p + z**2 / (2 * n) - z * ((p * (1 - p) + z**2 / (4 * n)) / n) ** 0.5
    return max(0.0, num / denom)


def main():
    # Step 1: load canonical_documents for document_id -> (created_at_utc, sentiment_label)
    print("Loading canonical_documents...", flush=True)
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    cur.execute("SELECT id, external_id, fetched_at, sentiment_label FROM canonical_documents WHERE sentiment_label IN ('bullish', 'bearish')")
    docs_by_id = {}
    docs_by_extid = {}
    for row in cur.fetchall():
        doc_id, ext_id, fetched_at, sentiment = row
        if not fetched_at:
            continue
        try:
            ts = datetime.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.UTC)
        except Exception:
            continue
        docs_by_id[doc_id] = (ts, sentiment)
        if ext_id:
            docs_by_extid[ext_id] = (ts, sentiment)
    conn.close()
    print(f"  documents indexed: by_id={len(docs_by_id)}, by_extid={len(docs_by_extid)}", flush=True)

    # Step 2: load regimes
    btc_regimes = load_regime(REGIME_BTC, "BTC")
    eth_regimes = load_regime(REGIME_ETH, "ETH")
    print(f"BTC regimes: {len(btc_regimes)}, ETH regimes: {len(eth_regimes)}", flush=True)
    print(f"BTC range: {btc_regimes[0][0]} -> {btc_regimes[-1][0]}", flush=True)

    # Step 3: walk outcomes, join
    cross_tab = defaultdict(lambda: Counter())  # (asset, regime) -> {hit, miss, inconclusive}
    vol_tab = defaultdict(lambda: Counter())    # (asset, vol_class) -> ...
    sentiment_regime_tab = defaultdict(lambda: Counter())  # (regime, sentiment) -> ...

    n_total = 0
    n_post_window = 0
    n_joined = 0
    n_missing_doc = 0
    n_missing_regime = 0
    n_no_asset = 0

    for line in OUTCOMES.read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        n_total += 1
        asset_raw = r.get("asset", "") or ""
        # Normalize: BTC/USDT, BTC-USD, BTC -> BTC (same for ETH)
        base = asset_raw.split("/")[0].split("-")[0].upper().strip()
        if base not in ("BTC", "ETH"):
            n_no_asset += 1
            continue
        asset = base
        doc_id = r.get("document_id")
        doc_meta = docs_by_id.get(doc_id) or docs_by_extid.get(doc_id)
        if not doc_meta:
            n_missing_doc += 1
            continue
        doc_ts, sentiment = doc_meta
        if doc_ts < WINDOW_START:
            continue
        n_post_window += 1
        regime_rows = btc_regimes if asset == "BTC" else eth_regimes
        regime, vol_class = regime_lookup(regime_rows, doc_ts)
        if regime is None:
            n_missing_regime += 1
            continue
        n_joined += 1
        outcome = r.get("outcome", "?")
        cross_tab[(asset, regime)][outcome] += 1
        vol_tab[(asset, vol_class)][outcome] += 1
        sentiment_regime_tab[(regime, sentiment)][outcome] += 1

    print(flush=True)
    print(f"=== Join Statistics ===", flush=True)
    print(f"total outcomes: {n_total}", flush=True)
    print(f"  no BTC/ETH asset: {n_no_asset}", flush=True)
    print(f"  missing doc_id mapping: {n_missing_doc}", flush=True)
    print(f"  post-window (>=2026-05-09): {n_post_window}", flush=True)
    print(f"  missing regime within 1.5h: {n_missing_regime}", flush=True)
    print(f"  successfully joined: {n_joined}", flush=True)
    print(flush=True)

    print(f"=== Cross-Tab: Precision per (Asset, Regime) ===", flush=True)
    print(f"{'asset':5s} {'regime':18s} {'hit':>5s} {'miss':>5s} {'inc':>5s} {'n_res':>5s} {'prec':>7s} {'wilson_lo':>10s}", flush=True)
    for key in sorted(cross_tab.keys()):
        asset, regime = key
        c = cross_tab[key]
        hits, misses, incs = c["hit"], c["miss"], c["inconclusive"]
        n_res = hits + misses
        prec = hits / n_res if n_res else 0.0
        wlo = wilson_lower(hits, n_res)
        print(f"{asset:5s} {regime:18s} {hits:>5d} {misses:>5d} {incs:>5d} {n_res:>5d} {prec*100:>6.1f}% {wlo*100:>9.1f}%", flush=True)

    print(flush=True)
    print(f"=== Cross-Tab: Precision per (Asset, Vol-Class) ===", flush=True)
    print(f"{'asset':5s} {'vol_class':12s} {'hit':>5s} {'miss':>5s} {'inc':>5s} {'n_res':>5s} {'prec':>7s} {'wilson_lo':>10s}", flush=True)
    for key in sorted(vol_tab.keys()):
        asset, vol_class = key
        c = vol_tab[key]
        hits, misses, incs = c["hit"], c["miss"], c["inconclusive"]
        n_res = hits + misses
        prec = hits / n_res if n_res else 0.0
        wlo = wilson_lower(hits, n_res)
        print(f"{asset:5s} {vol_class:12s} {hits:>5d} {misses:>5d} {incs:>5d} {n_res:>5d} {prec*100:>6.1f}% {wlo*100:>9.1f}%", flush=True)

    print(flush=True)
    print(f"=== Sentiment x Regime ===", flush=True)
    print(f"{'regime':18s} {'sent':8s} {'hit':>5s} {'miss':>5s} {'n_res':>5s} {'prec':>7s}", flush=True)
    for key in sorted(sentiment_regime_tab.keys()):
        regime, sent = key
        c = sentiment_regime_tab[key]
        hits, misses = c["hit"], c["miss"]
        n_res = hits + misses
        if n_res < 5:
            continue
        prec = hits / n_res if n_res else 0.0
        print(f"{regime:18s} {sent:8s} {hits:>5d} {misses:>5d} {n_res:>5d} {prec*100:>6.1f}%", flush=True)

    print(flush=True)
    print(f"=== Baseline (all joined) ===", flush=True)
    all_hits = sum(c["hit"] for c in cross_tab.values())
    all_misses = sum(c["miss"] for c in cross_tab.values())
    all_n = all_hits + all_misses
    if all_n:
        print(f"overall: {all_hits} hit / {all_misses} miss / n_res {all_n} / precision {all_hits/all_n*100:.1f}%", flush=True)
    else:
        print(f"overall: 0 joined records — D2-R4 not analyzable with current data", flush=True)


if __name__ == "__main__":
    main()
