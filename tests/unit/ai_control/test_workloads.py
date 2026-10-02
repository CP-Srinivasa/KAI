from datetime import UTC, datetime, timedelta
from typing import Any

from app.ai.control.workloads import WorkloadKey, aggregate, provider_activity

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def zeile(min_alt: float, **kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "ts": (NOW - timedelta(minutes=min_alt)).isoformat(),
        "purpose": "analysis",
        "logical_route": "standard",
        "service": "kai-server",
        "transport": "direct",
        "provider": "openai",
        "actual_provider": "openai",
        "model": "gpt-4o",
        "actual_model": "gpt-4o",
        "ok": True,
        "cost_usd": 0.0085,
        "input_tokens": 2000,
        "output_tokens": 300,
        "source": "cryptobriefing",
        "chain_position": 0,
    }
    r.update(kw)
    return r


def test_summe_je_schluessel() -> None:
    rows = [
        zeile(5),
        zeile(10, source="coindesk"),
        zeile(20, ok=False, cost_usd=None, input_tokens=0, output_tokens=0, error_class="server"),
    ]
    agg = aggregate(rows, since=NOW - timedelta(hours=1), until=NOW)
    st = agg[WorkloadKey("analysis", "standard", "kai-server", "direct", "gpt-4o")]
    assert (st.calls, st.ok, st.failures) == (3, 2, 1)
    assert round(st.known_cost_usd, 4) == 0.017 and st.input_tokens == 4000
    assert st.sources == {"cryptobriefing": 2, "coindesk": 1}
    assert st.last_ok == NOW - timedelta(minutes=5)
    assert round(st.approx_kb, 1) == round(4600 * 4 / 1024, 1)


def test_alte_und_fremde_zeilen_landen_in_unbekannt() -> None:
    alt = {"ts": (NOW - timedelta(minutes=1)).isoformat(), "provider": "openai", "ok": True}
    fremd = zeile(2, purpose="wahrsagen", logical_route=None, transport=None, service=None)
    agg = aggregate([alt, fremd], since=NOW - timedelta(hours=1), until=NOW)
    assert {k.purpose for k in agg} == {"unbekannt", "wahrsagen"}
    assert all(k.service == "unbekannt" and k.transport == "direct" for k in agg)


def test_fallback_wird_gezaehlt() -> None:
    agg = aggregate(
        [zeile(1, transport="litellm", fallback_to="direct", ok=False)],
        since=NOW - timedelta(hours=1),
        until=NOW,
    )
    assert next(iter(agg.values())).fallbacks == 1


def test_anbieter_aktivitaet() -> None:
    rows = [
        zeile(
            m,
            ok=False,
            cost_usd=None,
            error_class="quota",
            actual_provider="deepseek",
            provider="deepseek",
        )
        for m in (1, 2, 3, 4, 5)
    ]
    a = provider_activity(rows, now=NOW)["deepseek"]
    assert a.consecutive_failures == 5 and a.quota_errors_24h == 5 and a.calls_1h == 5
