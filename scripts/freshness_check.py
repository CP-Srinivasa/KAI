"""KAI Dashboard freshness self-test.

Independently probes every endpoint the dashboard polls, extracts the
timestamp each one exposes, and reports staleness against per-endpoint
thresholds. Designed to be safe to run from cron — no writes to DB or
audit files, no expensive recomputation, just GETs against loopback.

Usage:
    python scripts/freshness_check.py            # human-readable
    python scripts/freshness_check.py --json     # machine output

Exit codes:
    0  all probes within OK threshold
    2  at least one WARN (drifting but not critical)
    1  at least one CRIT (dashboard would show stale data) or unreachable

Side effects:
    - writes artifacts/freshness_status.json (last status snapshot)
    - appends one summary line per run to logs/freshness_check.log
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = REPO_ROOT / "artifacts"
LOGS = REPO_ROOT / "logs"
STATUS_FILE = ARTIFACTS / "freshness_status.json"
LOG_FILE = LOGS / "freshness_check.log"


@dataclass(frozen=True)
class Probe:
    name: str
    path: str
    timestamp_field: str | None  # dotted path inside the JSON, None = no TS
    warn_after_s: int  # >= warn_after_s → WARN
    crit_after_s: int  # >= crit_after_s → CRIT


PROBES: tuple[Probe, ...] = (
    # Health is HTTP-only; we don't expect a payload timestamp.
    Probe("health", "/health", None, 0, 0),
    # Quality bar — TTL cache 30s + frontend polls every 30s, so >120s = stale.
    Probe("dashboard_quality", "/dashboard/api/quality", "generated_at", 120, 600),
    # Portfolio + exposure are computed live from audit + price provider.
    Probe("portfolio_snapshot", "/operator/portfolio-snapshot", "generated_at", 240, 600),
    Probe("exposure_summary", "/operator/exposure-summary", "generated_at", 240, 600),
    # Trading loop is driven by KAI-PaperTrading (every 10min). Anything
    # older than ~14 min suggests the cron stopped firing.
    Probe(
        "trading_loop_status", "/operator/trading-loop/status", "last_cycle_completed_at", 840, 1800
    ),
)


def _read_token() -> str:
    token = os.environ.get("KAI_FRESHNESS_TOKEN", "").strip()
    if token:
        return token
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("APP_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _extract_field(payload: object, dotted: str) -> str | None:
    cur: object = payload
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return str(cur) if cur is not None else None


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Result:
    name: str
    path: str
    http_status: int
    timestamp: str | None
    age_seconds: float | None
    state: str  # ok | warn | crit | down | no_ts
    note: str = ""

    @property
    def state_rank(self) -> int:
        return {"ok": 0, "no_ts": 0, "warn": 2, "crit": 1, "down": 1}[self.state]


def probe_one(client: httpx.Client, p: Probe, now: datetime) -> Result:
    try:
        r = client.get(p.path, timeout=10.0)
    except httpx.HTTPError as exc:
        return Result(
            p.name, p.path, 0, None, None, "down", f"http_error: {exc.__class__.__name__}"
        )

    if r.status_code != 200:
        return Result(p.name, p.path, r.status_code, None, None, "down", f"status {r.status_code}")

    if p.timestamp_field is None:
        return Result(p.name, p.path, 200, None, None, "no_ts")

    try:
        body = r.json()
    except ValueError:
        return Result(p.name, p.path, 200, None, None, "down", "non-json body")

    raw_ts = _extract_field(body, p.timestamp_field)
    if raw_ts is None:
        return Result(
            p.name, p.path, 200, None, None, "down", f"missing field '{p.timestamp_field}'"
        )

    parsed = _parse_iso(raw_ts)
    if parsed is None:
        return Result(p.name, p.path, 200, raw_ts, None, "down", "unparseable timestamp")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age = (now - parsed).total_seconds()

    if age >= p.crit_after_s:
        state = "crit"
    elif age >= p.warn_after_s:
        state = "warn"
    else:
        state = "ok"
    return Result(p.name, p.path, 200, raw_ts, age, state)


def write_outputs(results: list[Result], overall: str) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "checked_at": datetime.now(UTC).isoformat(),
        "overall": overall,
        "probes": [asdict(r) for r in results],
    }
    STATUS_FILE.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    line_bits = [datetime.now(UTC).isoformat(), overall]
    for r in results:
        age = f"{r.age_seconds:.0f}s" if r.age_seconds is not None else "-"
        line_bits.append(f"{r.name}={r.state}({age})")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write("\t".join(line_bits) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base", default=os.environ.get("KAI_FRESHNESS_BASE", "http://127.0.0.1:8000")
    )
    parser.add_argument("--json", action="store_true", help="JSON output to stdout")
    args = parser.parse_args()

    token = _read_token()
    if not token:
        print("ERROR: no APP_API_KEY available (env KAI_FRESHNESS_TOKEN or .env)", file=sys.stderr)
        return 1

    headers = {"Authorization": f"Bearer {token}"}
    now = datetime.now(UTC)
    results: list[Result] = []
    with httpx.Client(base_url=args.base, headers=headers) as client:
        for p in PROBES:
            results.append(probe_one(client, p, now))

    states = {r.state for r in results}
    if "down" in states or "crit" in states:
        overall = "crit"
        exit_code = 1
    elif "warn" in states:
        overall = "warn"
        exit_code = 2
    else:
        overall = "ok"
        exit_code = 0

    write_outputs(results, overall)

    if args.json:
        print(
            json.dumps(
                {
                    "overall": overall,
                    "probes": [asdict(r) for r in results],
                },
                indent=2,
            )
        )
    else:
        print(f"=== KAI Freshness ({overall.upper()}) {now.isoformat(timespec='seconds')} ===")
        for r in results:
            age = f"{r.age_seconds:6.1f}s" if r.age_seconds is not None else "    -- "
            tag = {
                "ok": "[OK]  ",
                "warn": "[WARN]",
                "crit": "[CRIT]",
                "down": "[DOWN]",
                "no_ts": "[--]  ",
            }[r.state]
            extra = f"  ({r.note})" if r.note else ""
            print(f"  {tag}  {r.name:24s} age={age}  http={r.http_status}{extra}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
