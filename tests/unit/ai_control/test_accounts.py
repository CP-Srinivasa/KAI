import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.observability.ai_control import accounts as ac
from app.observability.ai_control.config import AccountKeys

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
DEEPSEEK = {
    "is_available": True,
    "balance_infos": [
        {
            "currency": "USD",
            "total_balance": "21.61",
            "granted_balance": "0.00",
            "topped_up_balance": "21.61",
        }
    ],
}
MOONSHOT = {
    "code": 0,
    "data": {"available_balance": 23.78, "voucher_balance": 4.24, "cash_balance": 19.54},
    "scode": "0x0",
    "status": True,
}
OPENAI = {
    "object": "page",
    "data": [
        {"object": "bucket", "results": [{"amount": {"value": 1.21, "currency": "usd"}}]},
        {"object": "bucket", "results": [{"amount": {"value": 2.89, "currency": "usd"}}]},
    ],
    "has_more": False,
}


def test_parser() -> None:
    assert ac.parse_deepseek(DEEPSEEK)[:2] == (21.61, "USD")
    bal, cur, detail = ac.parse_moonshot(MOONSHOT)
    assert (bal, cur, detail["voucher_balance"]) == (23.78, "USD", 4.24)
    assert round(ac.parse_openai_costs(OPENAI), 2) == 4.10


def _client(antworten: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        for teil, antwort in antworten.items():
            if teil in str(request.url):
                return antwort
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_abfrage_ohne_schluessel_und_mit_fehler() -> None:
    keys = AccountKeys(
        _env_file=None, deepseek_api_key="sk-x", moonshot_api_key="", openai_admin_key=""
    )
    client = _client({"/user/balance": httpx.Response(401, json={"error": "nope"})})
    konten = {a.provider: a for a in ac.fetch_accounts(keys, client=client, now=NOW)}
    assert konten["deepseek"].status == "fehler" and konten["deepseek"].error == "HTTP 401"
    assert konten["moonshot"].status == "kein_schluessel"
    assert konten["openai"].status == "kein_api"
    assert "sk-x" not in json.dumps([a.__dict__ for a in konten.values()], default=str)


def test_openai_mit_admin_schluessel() -> None:
    keys = AccountKeys(
        _env_file=None, deepseek_api_key="", moonshot_api_key="", openai_admin_key="sk-admin"
    )
    client = _client({"/organization/costs": httpx.Response(200, json=OPENAI)})
    konten = {a.provider: a for a in ac.fetch_accounts(keys, client=client, now=NOW)}
    # Review I2: Monatskosten sind kein Guthaben -- sonst ist OpenAI am Monatsersten „leer“.
    assert konten["openai"].status == "ok" and konten["openai"].balance is None
    assert konten["openai"].detail["kind"] == "month_cost"
    assert round(konten["openai"].detail["month_cost_usd"], 2) == 4.10


def test_letzter_guter_wert_bleibt(tmp_path: Path) -> None:
    alt = [
        ac.Account(
            "deepseek",
            "ok",
            21.61,
            "USD",
            {},
            None,
            "2026-10-02T10:00:00+00:00",
            ac.TOPUP_URLS["deepseek"],
        )
    ]
    neu = [
        ac.Account(
            "deepseek",
            "fehler",
            None,
            None,
            {},
            "HTTP 500",
            NOW.isoformat(),
            ac.TOPUP_URLS["deepseek"],
        )
    ]
    gemischt = ac.merge_with_previous(neu, [a.__dict__ for a in alt])
    assert gemischt[0].balance == 21.61 and gemischt[0].status == "fehler"
    assert gemischt[0].detail["balance_from"] == "2026-10-02T10:00:00+00:00"
    pfad = tmp_path / "ai_accounts.json"
    ac.write_accounts(pfad, gemischt, NOW)
    gelesen, geschrieben = ac.read_accounts(pfad)
    assert gelesen[0]["balance"] == 21.61 and geschrieben == NOW


def test_openai_monatskosten_bleiben_bei_fehler() -> None:
    alt = [
        {
            "provider": "openai",
            "status": "ok",
            "balance": None,
            "currency": "USD",
            "detail": {"kind": "month_cost", "month_cost_usd": 4.1},
            "fetched_at": "2026-10-02T11:00:00+00:00",
        }
    ]
    neu = [ac.Account("openai", "fehler", None, None, {}, "HTTP 500", NOW.isoformat(), "u")]
    k = ac.merge_with_previous(neu, alt)[0]
    assert k.detail["month_cost_usd"] == 4.1
    assert k.detail["balance_from"] == "2026-10-02T11:00:00+00:00" and k.balance is None
