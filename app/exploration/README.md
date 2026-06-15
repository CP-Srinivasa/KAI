# Exploration Sandbox

Isolated, default-off research layer to **measure what candidate data sources
actually deliver** (via official APIs *and* operator-authorised grey-area
scrapers) before any source is graduated into the production pipeline.

- Plan / SSOT: `docs/strategy/source_intake_exploration_plan.md`
- Decision: `docs/adr/0006-source-intake-exploration-grey-area.md` (DEC-SRC-EXPLORE-001)

## Why it's separate

This package is fully isolated and reversible (`rm -rf app/exploration`):
- No production runtime module imports it; it imports only
  `app.exploration` / `app.security` / `app.core`.
- It has its own settings (`ExplorationSettings`, env prefix `EXPLORATION_`) and
  its own CLI — it is **not** wired into the production `trading-bot` CLI.
- Enforced by `tests/unit/test_exploration_import_isolation.py`.

## Hard lines (always in force, not covered by the §5 override)

- No login / paywall / auth bypass, no CAPTCHA-breaking.
- No DoS-near request rates — throttle + capture-cache are mandatory.
- No secrets in the repo. SSRF guard runs before every outbound call.

## Usage

```bash
# List eligible probes given current EXPLORATION_* env
python -m app.exploration.cli list

# Run one probe (no key needed — works against the real free API)
EXPLORATION_ENABLED=true EXPLORATION_COINGECKO_ENABLED=true \
  python -m app.exploration.cli run --only coingecko

# Run everything that's enabled
python -m app.exploration.cli run

# Build the coverage report from captured artifacts
python -m app.exploration.cli report
```

Artifacts (gitignored under `artifacts/`):
- `artifacts/exploration/raw/<probe_id>/<ts>.json` — unmodified payloads (audit)
- `artifacts/exploration/normalized/<probe_id>.jsonl` — flat records for the report
- `artifacts/exploration/coverage_report.{md,json}` — the actual product

## Sources & priorities

| Probe | Mode(s) | Key | Prio | Note |
|---|---|---|---|---|
| `coingecko` | api, scrape | optional | – | works keyless; tests the "pro key worth it?" finding |
| `coinglass` | api, scrape | required (api) | P0 | extends live V5 funding/OI evidence |
| `messari` | api, scrape | optional | P1 | metrics + news (two layers) |
| `dune` | api | required + query id | P1 | cached query results, no exec credits |
| `glassnode` | api, scrape | required (api) | P2 | free = Tier-1 only |
| `coinmarketcap` | api, scrape | required (api) | P3 | largely redundant w/ CoinGecko |
| `nansen` | api | required | P3 | documents the access wall honestly |

Adding a source: implement an `ExplorationProbe` in `sources/<name>.py`, register
it behind its settings flag in `sources/__init__.py`, add a parsing test.
