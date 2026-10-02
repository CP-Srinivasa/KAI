"""Guthaben je Anbieter -- vom Anbieter selbst (Spec §5.4). Nur der Timer ruft das auf."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx

from app.observability.ai_control.config import AccountKeys

TIMEOUT_S: Final = 10.0
TOPUP_URLS: Final = {
    "deepseek": "https://platform.deepseek.com/top_up",
    "moonshot": "https://platform.moonshot.ai/console/pay",
    "openai": "https://platform.openai.com/settings/organization/billing/overview",
}

Abfrage = Callable[[], tuple[float, str, dict[str, Any]]]


@dataclass
class Account:
    provider: str
    status: str  # ok | fehler | kein_schluessel | kein_api
    balance: float | None
    currency: str | None
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    fetched_at: str = ""
    topup_url: str = ""


def parse_deepseek(body: dict[str, Any]) -> tuple[float, str, dict[str, Any]]:
    info = (body.get("balance_infos") or [{}])[0]
    return (
        float(info["total_balance"]),
        str(info.get("currency") or "USD"),
        {
            "granted_balance": float(info.get("granted_balance") or 0),
            "topped_up_balance": float(info.get("topped_up_balance") or 0),
            "is_available": bool(body.get("is_available")),
        },
    )


def parse_moonshot(body: dict[str, Any]) -> tuple[float, str, dict[str, Any]]:
    data = body["data"]
    return (
        float(data["available_balance"]),
        "USD",
        {
            "cash_balance": float(data.get("cash_balance") or 0),
            "voucher_balance": float(data.get("voucher_balance") or 0),
        },
    )


def parse_openai_costs(body: dict[str, Any]) -> float:
    return sum(
        float(r["amount"]["value"]) for b in body.get("data") or [] for r in b.get("results") or []
    )


def _abfrage(client: httpx.Client, url: str, schluessel: str, **params: Any) -> dict[str, Any]:
    antwort = client.get(
        url,
        headers={"Authorization": f"Bearer {schluessel}"},
        params=params or None,
        timeout=TIMEOUT_S,
    )
    if antwort.status_code != 200:
        raise RuntimeError(f"HTTP {antwort.status_code}")
    daten = antwort.json()
    if not isinstance(daten, dict):
        raise ValueError("unerwartete Antwort")
    return daten


def fetch_accounts(keys: AccountKeys, *, client: httpx.Client, now: datetime) -> list[Account]:
    stempel = now.astimezone(UTC).isoformat()
    konten: list[Account] = []

    def versuch(provider: str, schluessel: str, holen: Abfrage) -> None:
        url = TOPUP_URLS[provider]
        if not schluessel:
            konten.append(Account(provider, "kein_schluessel", None, None, {}, None, stempel, url))
            return
        try:
            balance, waehrung, detail = holen()
        except Exception as exc:  # noqa: BLE001 -- ein Anbieter stoppt die anderen nicht
            # Nie str(exc) ungeprueft weitergeben: ein Fehlertext koennte die Anfrage samt
            # Kopfzeilen enthalten. Nur der eigene HTTP-Status oder der Fehlertyp.
            text = str(exc) if str(exc).startswith("HTTP ") else type(exc).__name__
            konten.append(Account(provider, "fehler", None, None, {}, text, stempel, url))
            return
        konten.append(Account(provider, "ok", balance, waehrung, detail, None, stempel, url))

    versuch(
        "deepseek",
        keys.deepseek_api_key,
        lambda: parse_deepseek(
            _abfrage(client, "https://api.deepseek.com/user/balance", keys.deepseek_api_key)
        ),
    )
    versuch(
        "moonshot",
        keys.moonshot_api_key,
        lambda: parse_moonshot(
            _abfrage(
                client,
                f"{keys.moonshot_api_base.rstrip('/')}/users/me/balance",
                keys.moonshot_api_key,
            )
        ),
    )
    if keys.openai_admin_key:
        monat = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        def openai() -> tuple[float, str, dict[str, Any]]:
            kosten = parse_openai_costs(
                _abfrage(
                    client,
                    "https://api.openai.com/v1/organization/costs",
                    keys.openai_admin_key,
                    start_time=int(monat.timestamp()),
                    bucket_width="1d",
                    limit=31,
                )
            )
            return kosten, "USD", {"kind": "month_cost"}

        versuch("openai", keys.openai_admin_key, openai)
    else:
        konten.append(
            Account(
                "openai",
                "kein_api",
                None,
                None,
                {"hint": "OPENAI_ADMIN_KEY fehlt"},
                None,
                stempel,
                TOPUP_URLS["openai"],
            )
        )
    return konten


def merge_with_previous(new: list[Account], previous: list[dict[str, Any]]) -> list[Account]:
    """Scheitert eine Abfrage, bleibt der letzte gute Wert -- mit seinem Zeitpunkt."""
    alt = {p.get("provider"): p for p in previous}
    for konto in new:
        vorher = alt.get(konto.provider) or {}
        if konto.status == "fehler" and vorher.get("balance") is not None:
            frueher = vorher.get("detail") or {}
            konto.balance = float(vorher["balance"])
            konto.currency = vorher.get("currency")
            konto.detail = {
                **frueher,
                "balance_from": frueher.get("balance_from") or vorher.get("fetched_at"),
            }
    return new


def write_accounts(path: Path, accounts: list[Account], now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    daten = {
        "schema": "ai-accounts/v1",
        "written_at": now.astimezone(UTC).isoformat(),
        "accounts": [asdict(a) for a in accounts],
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ai-accounts-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, sort_keys=True)
    os.replace(tmp, path)


def read_accounts(path: Path) -> tuple[list[dict[str, Any]], datetime | None]:
    try:
        daten = json.loads(path.read_text(encoding="utf-8"))
        return list(daten.get("accounts") or []), datetime.fromisoformat(daten["written_at"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [], None


__all__ = [
    "TOPUP_URLS",
    "Account",
    "fetch_accounts",
    "merge_with_previous",
    "parse_deepseek",
    "parse_moonshot",
    "parse_openai_costs",
    "read_accounts",
    "write_accounts",
]
