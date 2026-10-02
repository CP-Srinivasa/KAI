import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability.ai_control.config import ControlPaths
from app.observability.ai_control.digest import collect, format_lines

NOW = datetime(2026, 10, 2, 5, 30, tzinfo=UTC)


def test_zwei_zeilen() -> None:
    block = {
        "yesterday": {
            "day": "2026-10-01",
            "cost_by_provider": {"openai": 1.24},
            "calls": 145,
            "input_tokens": 312000,
            "output_tokens": 46000,
            "budget_exhausted_at": "2026-10-01T13:55:00+00:00",
        },
        "accounts": [
            {"provider": "deepseek", "balance": 21.61, "runway_days": None, "status": "ok"},
            {"provider": "moonshot", "balance": 19.3, "runway_days": 9.0, "status": "ok"},
            {"provider": "openai", "balance": None, "runway_days": None, "status": "kein_api"},
        ],
        "open_hints": 2,
    }
    gestern, konten = format_lines(block)
    assert gestern == (
        "🧠 *KI gestern:* 1.24 $ (openai 1.24) · 145 Aufrufe · Token 312k/46k · "
        "Budgetende 13:55 UTC"
    )
    assert (
        konten == "🏦 *KI-Konten:* deepseek 21.61 $ · moonshot 19.30 $ (~9 T) · offen: 2 Hinweise"
    )


def test_fehler_wird_eine_zeile() -> None:
    assert format_lines({"error": "OSError"}) == ["🧠 *KI-Kontrolle:* nicht lesbar (OSError)"]


def test_collect_aus_dateien(tmp_path: Path) -> None:
    paths = ControlPaths(
        telemetry=tmp_path / "t.jsonl",
        accounts=tmp_path / "a.json",
        runtime_dir=tmp_path / "rt",
        protocol=tmp_path / "rt" / "p.json",
        alert_state=tmp_path / "rt" / "s.json",
        env_file=tmp_path / ".env",
    )
    gestern = (NOW - timedelta(days=1)).replace(hour=9)
    paths.telemetry.write_text(
        json.dumps(
            {
                "ts": gestern.isoformat(),
                "provider": "openai",
                "actual_provider": "openai",
                "ok": True,
                "cost_usd": 0.7,
                "cost_status": "OK",
                "purpose": "analysis",
                "chain_position": 0,
                "correlation_id": "d1",
                "input_tokens": 100,
                "output_tokens": 10,
            }
        )
        + "\n"
    )
    paths.accounts.write_text(
        json.dumps(
            {
                "schema": "ai-accounts/v1",
                "written_at": NOW.isoformat(),
                "accounts": [{"provider": "deepseek", "status": "ok", "balance": 7.0}],
            }
        )
    )
    from app.ai import spend

    spend.reset_spend_cache()
    block = collect(NOW, paths)
    assert block["yesterday"]["cost_by_provider"] == {"openai": 0.7}
    assert block["accounts"][0]["runway_days"] is None  # deepseek ohne Verbrauch: unbegrenzt
    assert block["open_hints"] == 0
