"""Edge-Beweis Telegram notifier (Pi ops).

Pushes the autonomous_generator edge state (n, IC, EV) plus the live runtime
lever status. Deployed on the prod-Pi at ``/home/ubuntu/edge_ic_notify.py`` and
run by the ``kai-edge-ic-check`` timer via the repo venv.

The lever block is derived purely from ``.env`` at run time — it replaces the
former hardcoded ``Hebel live: a+b (...)`` line, which drifted from reality
after lever ``a`` was reverted (natural sizing) on 2026-06-18. This script is
READ-ONLY: it reports config state, it never writes or changes any trading,
risk, execution, cap or sizing parameter. A key absent from ``.env`` renders as
``UNKNOWN`` — never a fabricated default.

Run modes:
    python scripts/edge_ic_notify.py             # compose + send to Telegram
    python scripts/edge_ic_notify.py --dry-run   # compose + print, no send
    python scripts/edge_ic_notify.py --self-test  # pure-logic smoke, no net/CLI
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path.home() / "ai_analyst_trading_bot"
ENV_PATH = REPO / ".env"


def _env_map(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file into ``{KEY: raw_value}``. Missing/unreadable file
    -> ``{}`` so every key is treated as absent (UNKNOWN), never defaulted."""
    m: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            m[k.strip()] = v.strip()
    except (FileNotFoundError, OSError):
        return {}
    return m


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v.strip().strip('"').strip("'"))
    except ValueError:
        return None


def _bool(v: str | None) -> bool | None:
    if v is None:
        return None
    s = v.strip().strip('"').strip("'").lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return None


def lever_a(env: dict[str, str]) -> str:
    """Sizing-shrink throughput lever. LIVE if any sub-knob shrinks per-trade
    notional below the natural baseline (risk_pct < 0.25, or a positive
    min-stop-for-sizing floor, or a positive per-trade notional cap). OFF when
    all deciding keys are present and none shrink; UNKNOWN if any is absent."""
    checks = [
        (_num(env.get("RISK_MAX_RISK_PER_TRADE_PCT")), lambda x: x < 0.25),
        (_num(env.get("RISK_MIN_STOP_PCT_FOR_SIZING")), lambda x: x > 0),
        (_num(env.get("RISK_MAX_NOTIONAL_PER_TRADE_USD")), lambda x: x > 0),
    ]
    unknown = False
    for val, pred in checks:
        if val is None:
            unknown = True
        elif pred(val):
            return "LIVE"
    return "UNKNOWN" if unknown else "OFF"


def lever_b(env: dict[str, str]) -> str:
    """Regime time-stop throughput lever (EXECUTION_REGIME_EXIT_ENABLED)."""
    v = _bool(env.get("EXECUTION_REGIME_EXIT_ENABLED"))
    if v is None:
        return "UNKNOWN"
    return "LIVE" if v else "OFF"


def bearish_short_gate(env: dict[str, str]) -> str:
    """IC lever: LIVE when bearish/short news is suppressed
    (ALERT_ALLOW_SHORT_NEWS = false)."""
    v = _bool(env.get("ALERT_ALLOW_SHORT_NEWS"))
    if v is None:
        return "UNKNOWN"
    return "LIVE" if v is False else "OFF"


def lever_lines(env: dict[str, str]) -> list[str]:
    """Status block for the digest — factual, one lever per line, with the
    driving ``.env`` values shown so it is self-auditable. No edge/IC
    interpretation, no hardcoded 'a+b live' claim."""
    return [
        "Runtime-Hebel (aus .env):",
        f"  a sizing-shrink    = {lever_a(env)}   "
        f"(risk_pct={env.get('RISK_MAX_RISK_PER_TRADE_PCT', 'fehlt')}, "
        f"min_stop={env.get('RISK_MIN_STOP_PCT_FOR_SIZING', 'fehlt')}, "
        f"max_notional={env.get('RISK_MAX_NOTIONAL_PER_TRADE_USD', 'fehlt')})",
        f"  b regime-time-stop = {lever_b(env)}   "
        f"(regime_exit_enabled={env.get('EXECUTION_REGIME_EXIT_ENABLED', 'fehlt')})",
        f"  bearish-short-gate = {bearish_short_gate(env)}   "
        f"(allow_short_news={env.get('ALERT_ALLOW_SHORT_NEWS', 'fehlt')})",
    ]


def _self_test() -> None:
    """Pure-logic smoke (no network, no CLI): synthetic OFF/LIVE/UNKNOWN cases
    plus the operator acceptance against the live ``.env``
    (a=OFF, b=LIVE, bearish-short-gate=LIVE)."""
    assert (
        lever_a(
            {
                "RISK_MAX_RISK_PER_TRADE_PCT": "0.25",
                "RISK_MIN_STOP_PCT_FOR_SIZING": "0",
                "RISK_MAX_NOTIONAL_PER_TRADE_USD": "0",
            }
        )
        == "OFF"
    )
    assert (
        lever_a(
            {
                "RISK_MAX_RISK_PER_TRADE_PCT": "0.10",
                "RISK_MIN_STOP_PCT_FOR_SIZING": "0",
                "RISK_MAX_NOTIONAL_PER_TRADE_USD": "0",
            }
        )
        == "LIVE"
    )
    assert (
        lever_a(
            {
                "RISK_MAX_RISK_PER_TRADE_PCT": "0.25",
                "RISK_MIN_STOP_PCT_FOR_SIZING": "4",
                "RISK_MAX_NOTIONAL_PER_TRADE_USD": "0",
            }
        )
        == "LIVE"
    )
    assert lever_a({}) == "UNKNOWN"
    assert lever_b({"EXECUTION_REGIME_EXIT_ENABLED": "true"}) == "LIVE"
    assert lever_b({"EXECUTION_REGIME_EXIT_ENABLED": "false"}) == "OFF"
    assert lever_b({}) == "UNKNOWN"
    assert bearish_short_gate({"ALERT_ALLOW_SHORT_NEWS": "false"}) == "LIVE"
    assert bearish_short_gate({"ALERT_ALLOW_SHORT_NEWS": "true"}) == "OFF"
    assert bearish_short_gate({}) == "UNKNOWN"
    env = _env_map(ENV_PATH)
    a, b, gate = lever_a(env), lever_b(env), bearish_short_gate(env)
    got = (a, b, gate)
    print(f"live .env -> a={a} b={b} gate={gate}")
    print("\n".join(lever_lines(env)))
    assert got == ("OFF", "LIVE", "LIVE"), f"acceptance failed: {got}"
    print("SELF-TEST OK")


def main() -> None:
    if "--self-test" in sys.argv:
        _self_test()
        return
    dry = "--dry-run" in sys.argv

    token = (
        os.environ.get("ALERT_TELEGRAM_TOKEN")
        or os.environ.get("OPERATOR_TELEGRAM_BOT_TOKEN")
        or ""
    )
    chat = (
        os.environ.get("ALERT_TELEGRAM_CHAT_ID")
        or os.environ.get("OPERATOR_ADMIN_CHAT_IDS", "").split(",")[0].strip()
    )
    if not dry and (not token or not chat):
        print("notify: no telegram creds")
        return
    try:
        out = subprocess.run(
            [str(REPO / ".venv/bin/python"), "-m", "app.cli.main", "trading", "generator-edge"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=120,
        )
        d = json.loads(out.stdout)
    except Exception as exc:  # noqa: BLE001 — notifier must degrade, never raise
        print("notify: edge run failed", exc)
        return
    prof = next(
        (p for p in d.get("profiles", []) if p.get("cohort_key") == "autonomous_generator"), None
    )
    if not prof:
        print("notify: no generator cohort")
        return
    gate = d.get("gate_config", {})
    ic = prof.get("ic_by_horizon", {})
    ic_pos = sum(1 for v in ic.values() if isinstance(v, (int, float)) and v > 0)
    ic_str = " ".join(f"{k}:{v:+.3f}" for k, v in ic.items() if isinstance(v, (int, float)))
    lines = [
        "KAI Edge-Beweis — n + IC",
        f"autonomous_generator resolved n={prof.get('resolved_count')}/{gate.get('min_resolved', 30)}",
        f"EV_net={prof.get('expected_value_after_costs_bps')}bps  win={prof.get('win_rate')}",
        f"IC {ic_str}",
        f"IC-Horizonte positiv: {ic_pos}/{gate.get('min_ic_horizons_positive', '?')} benoetigt  "
        f"(Brier={prof.get('brier_score')} ECE={prof.get('calibration_error')})",
    ]
    lines += lever_lines(_env_map(ENV_PATH))
    msg = "\n".join(lines)
    if dry:
        print(msg)
        return
    try:
        import httpx

        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg},
            timeout=15,
        )
        print("notify: telegram sent ->", msg.replace(chr(10), " | "))
    except Exception as exc:  # noqa: BLE001 — send failure must not crash the timer
        print("notify: send failed", exc)


if __name__ == "__main__":
    main()
