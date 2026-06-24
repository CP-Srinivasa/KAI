"""Sprint 3 — Truth-Oracle: UC-5 fee-series endpoint + S-002 mint-rate-limit.

Confirms the receive side is gated (fee-series 503 while L402 off) and that the
S-002 mint guard caps invoice minting BEFORE one is issued (429 once the per-key
window cap is exhausted) — the DoS/HTLC-flood guard the plan mandates pre-L402.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import truth_oracle as to
from app.api.routers.truth_oracle import reset_mint_limiter, router
from app.core.lightning_settings import LightningSettings


def _app() -> FastAPI:
    a = FastAPI()
    a.include_router(router)
    return a


def test_fee_series_disabled_returns_503() -> None:
    reset_mint_limiter()
    r = TestClient(_app()).get("/oracle/fee-series")
    assert r.status_code == 503  # L402 disabled by default → gated, no data leak


def test_mint_rate_limit_caps_minting(monkeypatch) -> None:
    fake = SimpleNamespace(
        lightning=LightningSettings(
            enabled=True,
            l402_enabled=True,
            l402_secret="s",
            l402_mint_per_min=2,
            l402_mint_budget_per_min=100,
            pay_enabled=False,  # mint path unprovisioned → 503, but rate-limit fires first
        )
    )
    monkeypatch.setattr(to, "get_settings", lambda: fake)
    monkeypatch.setattr("app.core.settings.get_settings", lambda: fake)
    reset_mint_limiter()

    client = TestClient(_app())
    codes = [client.get("/oracle/onchain-facts").status_code for _ in range(3)]
    # within the per-key cap (2): each attempt reaches the (unprovisioned) mint → 503
    assert codes[0] == 503 and codes[1] == 503
    # 3rd attempt exceeds the cap → blocked BEFORE minting
    assert codes[2] == 429
