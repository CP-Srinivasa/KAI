import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.ai.config import InferenceSettings
from app.observability.ai_control.config import ControlPaths, ControlThresholds, LiteLLMModels
from app.observability.ai_control.snapshot import build_snapshot

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _paths(tmp: Path) -> ControlPaths:
    return ControlPaths(
        telemetry=tmp / "t.jsonl",
        accounts=tmp / "a.json",
        runtime_dir=tmp / "rt",
        protocol=tmp / "rt" / "p.json",
        alert_state=tmp / "rt" / "s.json",
        env_file=tmp / ".env",
    )


def _bau(tmp: Path, **kw: Any) -> dict[str, Any]:
    return build_snapshot(
        now=NOW,
        paths=_paths(tmp),
        inference=InferenceSettings(
            enabled=True,
            mode_ceiling="primary",
            route_modes={"research": "advisory", "standard": "off"},
        ),
        transport=kw.pop("transport", None),
        thresholds=ControlThresholds(_env_file=None),  # type: ignore[call-arg]
        models=LiteLLMModels(  # type: ignore[call-arg]
            _env_file=None,
            standard_model="deepseek/deepseek-v4-flash",
            research_model="moonshot/kimi-k2.6",
        ),
        providers_configured={"openai": True, "anthropic": False, "gemini": False, "xai": True},
        **kw,
    )


def _nulls_ohne_grund(wert: Any, pfad: str, gruende: dict[str, str]) -> list[str]:
    if isinstance(wert, dict):
        return [
            x
            for k, v in wert.items()
            for x in _nulls_ohne_grund(v, f"{pfad}.{k}" if pfad else k, gruende)
        ]
    return [pfad] if wert is None and pfad not in gruende else []


def test_snapshot_ohne_telemetrie_hat_gruende(tmp_path: Path) -> None:
    snap = _bau(tmp_path)
    assert snap["schema"] == "ai-control/v1"
    assert _nulls_ohne_grund(snap["summary"], "summary", snap["null_reasons"]) == []
    analyse = next(w for w in snap["workloads"] if w["purpose"] == "analysis")
    assert analyse["state"] == "bereit" and analyse["calls_today"] == 0
    assert len(snap["workloads"]) == 6


def test_route_aus_ist_deaktiviert_und_proxy_unbekannt(tmp_path: Path) -> None:
    snap = _bau(tmp_path)
    alias = {a["alias"]: a for a in snap["connections"]["aliases"]}
    assert alias["kai-standard"]["state"] == "deaktiviert"
    assert alias["kai-kimi-research"]["state"] == "bereit"
    assert snap["connections"]["proxy"]["state"] is None
    assert "connections.proxy.state" in snap["null_reasons"]


def test_veraltete_konten_erzeugen_hinweis(tmp_path: Path) -> None:
    alt = (NOW - timedelta(hours=4)).isoformat()
    (tmp_path / "a.json").write_text(
        json.dumps(
            {
                "schema": "ai-accounts/v1",
                "written_at": alt,
                "accounts": [
                    {
                        "provider": "moonshot",
                        "status": "ok",
                        "balance": 3.0,
                        "currency": "USD",
                        "detail": {},
                        "error": None,
                        "fetched_at": alt,
                        "topup_url": "https://x",
                    }
                ],
            }
        )
    )
    keys = {h["key"] for h in _bau(tmp_path)["attention"]}
    assert {"konten_veraltet", "guthaben:moonshot"} <= keys


def test_proxy_tot_ist_gestoert_mit_mindestalter(tmp_path: Path) -> None:
    transport = {
        "transport": {
            "proxy_alive": False,
            "proxy_status_code": None,
            "version": "1.102.1",
            "lock_matches": True,
            "verified_at": NOW.isoformat(),
        },
        "routes": [],
    }
    snap = _bau(tmp_path, transport=transport)
    assert snap["connections"]["proxy"]["state"] == "gestoert"
    hinweis = next(h for h in snap["attention"] if h["key"] == "gestoert:proxy:litellm")
    assert hinweis["min_age_min"] == 5


def test_aufrufe_heute_stimmen_mit_dem_budget(tmp_path: Path) -> None:
    from app.ai import spend

    zeilen = [
        {
            "ts": (NOW - timedelta(minutes=m)).isoformat(),
            "purpose": "analysis",
            "provider": "openai",
            "actual_provider": "openai",
            "model": "gpt-4o",
            "ok": True,
            "cost_usd": 0.0085,
            "cost_status": "OK",
            "chain_position": 0,
            "correlation_id": f"doc_{m}",
            "input_tokens": 2000,
            "output_tokens": 300,
        }
        for m in (5, 30, 90)
    ]
    (tmp_path / "t.jsonl").write_text("\n".join(json.dumps(z) for z in zeilen) + "\n")
    spend.reset_spend_cache()
    snap = _bau(tmp_path)
    analyse = next(w for w in snap["workloads"] if w["purpose"] == "analysis")
    heute, _monat = spend.current_spend(path=tmp_path / "t.jsonl", now=NOW)
    assert analyse["calls_today"] == heute.calls == 3
    assert analyse["state"] == "aktiv"
    openai = next(p for p in snap["connections"]["providers"] if p["name"] == "openai")
    assert openai["state"] == "aktiv"
