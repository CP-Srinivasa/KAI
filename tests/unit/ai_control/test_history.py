from datetime import UTC, datetime

from app.ai.control.history import budget_exhausted_at, daily

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def test_tage_und_budgetende() -> None:
    rows = [
        {
            "ts": "2026-10-01T09:00:00+00:00",
            "provider": "openai",
            "cost_usd": 0.5,
            "ok": True,
            "input_tokens": 10,
            "output_tokens": 1,
        },
        {
            "ts": "2026-10-01T13:55:00+00:00",
            "provider": "",
            "ok": False,
            "budget_decision": "reject:alert_reserve_exhausted",
        },
        {
            "ts": "2026-10-01T14:10:00+00:00",
            "provider": "",
            "ok": False,
            "budget_decision": "reject:alert_reserve_exhausted",
        },
        {"ts": "2026-10-02T08:00:00+00:00", "provider": "openai", "cost_usd": 0.25, "ok": True},
    ]
    tage = {t.day: t for t in daily(rows, now=NOW, days=2)}
    assert tage["2026-10-01"].cost_by_provider == {"openai": 0.5}
    assert tage["2026-10-01"].budget_exhausted_at == "2026-10-01T13:55:00+00:00"
    assert tage["2026-10-02"].budget_exhausted_at is None
    ende = budget_exhausted_at(rows, day="2026-10-01")
    assert ende is not None and ende.hour == 13
