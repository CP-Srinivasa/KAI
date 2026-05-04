"""KAI Dashboard freshness self-test.

Independently probes every endpoint the dashboard polls, extracts the
timestamp each one exposes, and reports staleness against per-endpoint
thresholds. Designed to be safe to run from cron — no writes to DB or
audit files, no expensive recomputation.

Default mode probes loopback only (service truth). Pass --external-base or set
KAI_FRESHNESS_EXTERNAL_BASE to probe the public edge in the same run (operator
reachability truth). The combined overall is intentionally strict: any internal
or external DOWN/CRIT makes the whole run CRIT, so a Cloudflare Access or tunnel
break cannot hide behind a green loopback probe.

Usage:
    python scripts/freshness_check.py                              # loopback only
    python scripts/freshness_check.py --json                       # machine output
    python scripts/freshness_check.py --external-base https://...  # loopback + edge

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
    return _read_env_key("APP_API_KEY")


def _read_env_key(key: str) -> str:
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _read_cf_access_headers() -> dict[str, str]:
    """Optional Cloudflare Access service-token headers for external probes."""
    client_id = (
        os.environ.get("KAI_FRESHNESS_CF_ACCESS_CLIENT_ID", "").strip()
        or os.environ.get("CF_ACCESS_CLIENT_ID", "").strip()
        or _read_env_key("KAI_FRESHNESS_CF_ACCESS_CLIENT_ID")
        or _read_env_key("CF_ACCESS_CLIENT_ID")
    )
    client_secret = (
        os.environ.get("KAI_FRESHNESS_CF_ACCESS_CLIENT_SECRET", "").strip()
        or os.environ.get("CF_ACCESS_CLIENT_SECRET", "").strip()
        or _read_env_key("KAI_FRESHNESS_CF_ACCESS_CLIENT_SECRET")
        or _read_env_key("CF_ACCESS_CLIENT_SECRET")
    )
    if not client_id or not client_secret:
        return {}
    return {
        "CF-Access-Client-Id": client_id,
        "CF-Access-Client-Secret": client_secret,
    }


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
    scope: str = "internal"


def _is_cloudflare_access_login(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        return False
    body_head = response.text[:2000].lower()
    return "cloudflare access" in body_head or "sign in" in body_head and "cloudflare" in body_head


def _is_cloudflare_access_redirect(response: httpx.Response) -> bool:
    if response.status_code not in {301, 302, 303, 307, 308}:
        return False
    location = response.headers.get("location", "").lower()
    return "cloudflareaccess.com" in location or "/cdn-cgi/access/login" in location


def _request_sent_cf_access_token(response: httpx.Response) -> bool:
    try:
        request = response.request
    except RuntimeError:
        return False
    return (
        bool(request.headers.get("CF-Access-Client-Id"))
        and bool(request.headers.get("CF-Access-Client-Secret"))
    )


def _cloudflare_access_note(response: httpx.Response) -> str:
    if _request_sent_cf_access_token(response):
        return "cloudflare_access_service_token_rejected"
    if _is_cloudflare_access_redirect(response):
        return "cloudflare_access_redirect"
    return "cloudflare_access_login"


def _non_json_note(response: httpx.Response) -> str:
    if _is_cloudflare_access_redirect(response) or _is_cloudflare_access_login(response):
        return _cloudflare_access_note(response)
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type:
        return f"non-json body ({content_type})"
    return "non-json body"


def probe_one(
    client: httpx.Client,
    p: Probe,
    now: datetime,
    *,
    scope: str = "internal",
) -> Result:
    try:
        r = client.get(p.path, timeout=10.0)
    except httpx.HTTPError as exc:
        return Result(
            p.name,
            p.path,
            0,
            None,
            None,
            "down",
            f"http_error: {exc.__class__.__name__}",
            scope,
        )

    if _is_cloudflare_access_redirect(r):
        return Result(
            p.name,
            p.path,
            r.status_code,
            None,
            None,
            "down",
            _cloudflare_access_note(r),
            scope,
        )

    if r.status_code != 200:
        return Result(
            p.name,
            p.path,
            r.status_code,
            None,
            None,
            "down",
            f"status {r.status_code}",
            scope,
        )

    if p.timestamp_field is None:
        if _is_cloudflare_access_login(r):
            return Result(p.name, p.path, 200, None, None, "down", _cloudflare_access_note(r), scope)
        return Result(p.name, p.path, 200, None, None, "no_ts", "", scope)

    try:
        body = r.json()
    except ValueError:
        return Result(p.name, p.path, 200, None, None, "down", _non_json_note(r), scope)

    raw_ts = _extract_field(body, p.timestamp_field)
    if raw_ts is None:
        return Result(
            p.name,
            p.path,
            200,
            None,
            None,
            "down",
            f"missing field '{p.timestamp_field}'",
            scope,
        )

    parsed = _parse_iso(raw_ts)
    if parsed is None:
        return Result(p.name, p.path, 200, raw_ts, None, "down", "unparseable timestamp", scope)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age = (now - parsed).total_seconds()

    if age >= p.crit_after_s:
        state = "crit"
    elif age >= p.warn_after_s:
        state = "warn"
    else:
        state = "ok"
    return Result(p.name, p.path, 200, raw_ts, age, state, "", scope)


def overall_from_results(results: list[Result]) -> tuple[str, int]:
    states = {r.state for r in results}
    if "down" in states or "crit" in states:
        return "crit", 1
    if "warn" in states:
        return "warn", 2
    return "ok", 0


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
        line_bits.append(f"{r.scope}.{r.name}={r.state}({age})")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write("\t".join(line_bits) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base", default=os.environ.get("KAI_FRESHNESS_BASE", "http://127.0.0.1:8000")
    )
    parser.add_argument(
        "--external-base",
        default=os.environ.get("KAI_FRESHNESS_EXTERNAL_BASE", "").strip(),
        help=(
            "Optional public base URL to probe in the same run. If set, any "
            "external failure contributes to the same overall status."
        ),
    )
    parser.add_argument("--json", action="store_true", help="JSON output to stdout")
    args = parser.parse_args()

    token = _read_token()
    if not token:
        print("ERROR: no APP_API_KEY available (env KAI_FRESHNESS_TOKEN or .env)", file=sys.stderr)
        return 1

    headers = {"Authorization": f"Bearer {token}", **_read_cf_access_headers()}
    now = datetime.now(UTC)
    results: list[Result] = []
    # follow_redirects=False is load-bearing: Cloudflare Access detection in
    # _is_cloudflare_access_redirect requires the 302 to be visible. Following
    # the redirect would land us on the IDP login page (possibly a third party)
    # where the body-based fallback is brittle.
    with httpx.Client(base_url=args.base, headers=headers, follow_redirects=False) as client:
        for p in PROBES:
            results.append(probe_one(client, p, now, scope="internal"))

    if args.external_base:
        with httpx.Client(
            base_url=args.external_base, headers=headers, follow_redirects=False
        ) as client:
            for p in PROBES:
                results.append(probe_one(client, p, now, scope="external"))

    overall, exit_code = overall_from_results(results)

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
            print(
                f"  {tag}  {r.scope:8s} {r.name:24s} "
                f"age={age}  http={r.http_status}{extra}"
            )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
