"""Smoke-checks für Phase-0 Step 12 Tabletop-Drill.

Operator-Bookkeeping: vor jedem Drill-Run + nach jedem Wieder-Aufbau
(Migration, Recovery) sicherstellt dieses Script, dass der Code-State
mit dem im Drill-Skript dokumentierten Erwartungen übereinstimmt.

Beispiel-Lauf:
    python -m scripts.security.tabletop_drill_checks
    python -m scripts.security.tabletop_drill_checks --component live_caps
    python -m scripts.security.tabletop_drill_checks --strict   # exit-nonzero on any warn

Es sind reine Static-Code- + Existenz-Checks. Sie ersetzen weder pytest
noch den eigentlichen Drill. Sie fangen aber sehr-billig: "Hat irgendwer
die Hard-Caps verändert?" und "Existiert der erwartete HOTP-Counter-Pfad?"
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {
    "MAX_POSITION_USD": 200.0,
    "MAX_OPEN_POSITIONS": 2,
    "LIVE_TRADING_DEFAULT_ENABLED": False,
    "LIVE_MODE_IDLE_LOCK_SECONDS": 3600,
}


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str

    @property
    def is_blocking(self) -> bool:
        return self.status == "fail"


def _glyph(status: str) -> str:
    return {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]"}.get(status, "[?]")


# ─── Component checks ────────────────────────────────────────────────────────


def check_live_caps() -> list[CheckResult]:
    """Hardcoded constants in live_caps.py must match EXPECTED values."""
    try:
        module = importlib.import_module("app.security.live_caps")
    except ImportError as exc:
        return [
            CheckResult(
                "live_caps.import",
                "fail",
                f"app.security.live_caps not importable: {exc}",
            )
        ]
    results: list[CheckResult] = []
    for const_name, expected_value in EXPECTED.items():
        actual = getattr(module, const_name, None)
        if actual is None:
            results.append(
                CheckResult(
                    f"live_caps.{const_name}",
                    "fail",
                    "constant missing in module",
                )
            )
        elif actual != expected_value:
            results.append(
                CheckResult(
                    f"live_caps.{const_name}",
                    "fail",
                    f"got {actual!r}, expected {expected_value!r}",
                )
            )
        else:
            results.append(
                CheckResult(
                    f"live_caps.{const_name}",
                    "ok",
                    f"= {actual!r}",
                )
            )
    return results


def check_live_engine_uses_caps() -> list[CheckResult]:
    """live_engine.py must call verify_live_order() in its order-send path."""
    path = REPO_ROOT / "app" / "execution" / "live_engine.py"
    if not path.exists():
        return [
            CheckResult(
                "live_engine.exists",
                "fail",
                f"{path} missing — N+3 PR not merged?",
            )
        ]
    text = path.read_text(encoding="utf-8")
    results: list[CheckResult] = []
    if "verify_live_order" in text:
        results.append(
            CheckResult(
                "live_engine.calls_verify_live_order",
                "ok",
                "verify_live_order() is referenced",
            )
        )
    else:
        results.append(
            CheckResult(
                "live_engine.calls_verify_live_order",
                "fail",
                "verify_live_order() NOT referenced — Cap-Check might be bypassed",
            )
        )
    return results


def check_hotp_module() -> list[CheckResult]:
    path = REPO_ROOT / "app" / "security" / "hotp_auth.py"
    results: list[CheckResult] = []
    if not path.exists():
        return [
            CheckResult(
                "hotp_auth.exists",
                "fail",
                f"{path} missing — N+2 PR not merged?",
            )
        ]
    text = path.read_text(encoding="utf-8")
    must_have = [
        (
            "counter monotonic guard",
            ("monotonic", "monotonie", "last_used_counter"),
        ),
        ("replay rejection", ("replay", "HotpReplayDetected")),
    ]
    for label, needles in must_have:
        hit = next((n for n in needles if n.lower() in text.lower()), None)
        if hit:
            results.append(
                CheckResult(
                    f"hotp_auth.{label.replace(' ', '_')}",
                    "ok",
                    f"marker '{hit}' present",
                )
            )
        else:
            results.append(
                CheckResult(
                    f"hotp_auth.{label.replace(' ', '_')}",
                    "warn",
                    f"none of {needles} found — verify defense in code review",
                )
            )
    return results


def check_exchange_perms_module() -> list[CheckResult]:
    path = REPO_ROOT / "app" / "security" / "exchange_perms.py"
    if not path.exists():
        return [
            CheckResult(
                "exchange_perms.exists",
                "fail",
                f"{path} missing — N+1 PR not merged?",
            )
        ]
    return [
        CheckResult(
            "exchange_perms.exists",
            "ok",
            f"{path.relative_to(REPO_ROOT)} present",
        )
    ]


def check_phase0_spec_present() -> list[CheckResult]:
    """The phase-0 spec docs must exist (read-only invariant)."""
    required_docs = [
        "docs/security/kai_light_live_phase0_spec.md",
        "docs/security/phase0_step12_tabletop_drill.md",
        "docs/security/live_trading_circuit_breaker_v1.md",
        "docs/security/red_team_response_v1.md",
        "docs/security/decision_log_20260509.md",
        "docs/security/operator_runbook_phase0.md",
    ]
    results: list[CheckResult] = []
    for rel in required_docs:
        if (REPO_ROOT / rel).exists():
            results.append(CheckResult(f"docs.{rel}", "ok", "present"))
        else:
            results.append(
                CheckResult(
                    f"docs.{rel}",
                    "warn",
                    "missing — drill log should reference complete spec set",
                )
            )
    return results


def check_live_audit_module() -> list[CheckResult]:
    path = REPO_ROOT / "app" / "execution" / "live_audit.py"
    if not path.exists():
        return [
            CheckResult(
                "live_audit.exists",
                "fail",
                f"{path} missing — N+4 PR not merged?",
            )
        ]
    text = path.read_text(encoding="utf-8")
    results = [
        CheckResult("live_audit.exists", "ok", "present"),
    ]
    if "live-v1" in text or "schema_version" in text:
        results.append(
            CheckResult(
                "live_audit.schema_versioned",
                "ok",
                "schema_version present in module",
            )
        )
    else:
        results.append(
            CheckResult(
                "live_audit.schema_versioned",
                "warn",
                "no 'schema_version' / 'live-v1' marker — verify in spec",
            )
        )
    return results


def check_live_default_disabled() -> list[CheckResult]:
    """LIVE_TRADING_DEFAULT_ENABLED MUST be False at module level."""
    try:
        module = importlib.import_module("app.security.live_caps")
    except ImportError as exc:
        return [
            CheckResult(
                "live_default_disabled",
                "fail",
                f"live_caps import failed: {exc}",
            )
        ]
    if getattr(module, "LIVE_TRADING_DEFAULT_ENABLED", True) is not False:
        return [
            CheckResult(
                "live_default_disabled",
                "fail",
                "LIVE_TRADING_DEFAULT_ENABLED is not False — Phase-0 invariant broken",
            )
        ]
    return [
        CheckResult(
            "live_default_disabled",
            "ok",
            "LIVE_TRADING_DEFAULT_ENABLED is False (boot-locked)",
        )
    ]


# ─── Runner ───────────────────────────────────────────────────────────────────


CHECKS: dict[str, Callable[[], list[CheckResult]]] = {
    "live_caps": check_live_caps,
    "live_engine": check_live_engine_uses_caps,
    "hotp_auth": check_hotp_module,
    "exchange_perms": check_exchange_perms_module,
    "phase0_spec": check_phase0_spec_present,
    "live_audit": check_live_audit_module,
    "live_default_disabled": check_live_default_disabled,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase-0 Step-12 Tabletop-Drill smoke-checks"
    )
    parser.add_argument(
        "--component",
        choices=list(CHECKS) + ["all"],
        default="all",
        help="Run only one component check (default: all)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat 'warn' as failure (exit non-zero on any warn)",
    )
    args = parser.parse_args()

    selected = [args.component] if args.component != "all" else list(CHECKS)
    sys.path.insert(0, str(REPO_ROOT))

    all_results: list[CheckResult] = []
    for component in selected:
        all_results.extend(CHECKS[component]())

    by_status: dict[str, int] = {"ok": 0, "warn": 0, "fail": 0}
    for r in all_results:
        print(f"{_glyph(r.status)} {r.name:<60s} {r.detail}")
        by_status[r.status] = by_status.get(r.status, 0) + 1

    print(
        f"\nSummary: ok={by_status['ok']}  "
        f"warn={by_status['warn']}  fail={by_status['fail']}"
    )

    if by_status["fail"] > 0:
        print("\n[BLOCKING] One or more checks FAILED — drill must NOT proceed.")
        return 2
    if args.strict and by_status["warn"] > 0:
        print("\n[STRICT] Warnings present — exiting non-zero per --strict.")
        return 1
    print("\nReady to proceed with tabletop drill.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
