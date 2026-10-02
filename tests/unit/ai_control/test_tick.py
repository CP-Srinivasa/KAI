import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.observability.ai_control.config import ControlPaths
from app.observability.ai_control.tick import run_tick

NOW = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)  # 12:00 MESZ, keine Ruhezeit


def _paths(tmp: Path) -> ControlPaths:
    return ControlPaths(
        telemetry=tmp / "t.jsonl",
        accounts=tmp / "a.json",
        runtime_dir=tmp / "rt",
        protocol=tmp / "rt" / "p.json",
        alert_state=tmp / "rt" / "s.json",
        env_file=tmp / ".env",
    )


def _client() -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"available_balance": 2.0, "voucher_balance": 0, "cash_balance": 2.0},
                },
            )
        )
    )


def test_tick_holt_konten_und_meldet_einmal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-m")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_ADMIN_KEY", raising=False)
    paths = _paths(tmp_path)
    paths.env_file.write_text("SOURCE_LLM_SPARFENSTER_MODE=enforce\n")
    gesendet: list[str] = []

    async def send(text: str) -> bool:
        gesendet.append(text)
        return True

    bericht = asyncio.run(run_tick(now=NOW, paths=paths, send=send, fetch_client=_client()))
    assert bericht["accounts_fetched"] is True and paths.accounts.exists()
    assert len(gesendet) == 1 and "Guthaben moonshot knapp" in gesendet[0]
    assert "sk-m" not in gesendet[0] and "sk-m" not in paths.accounts.read_text()
    bericht2 = asyncio.run(run_tick(now=NOW, paths=paths, send=send, fetch_client=_client()))
    assert bericht2["accounts_fetched"] is False and len(gesendet) == 1


def test_fehlgeschlagener_versand_wird_wiederholt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-m")
    paths = _paths(tmp_path)
    versuche: list[str] = []

    async def kaputt(text: str) -> bool:
        versuche.append(text)
        return False

    asyncio.run(run_tick(now=NOW, paths=paths, send=kaputt, fetch_client=_client()))
    asyncio.run(run_tick(now=NOW, paths=paths, send=kaputt, fetch_client=_client()))
    assert len(versuche) == 2, "nicht zugestellt = beim naechsten Lauf noch einmal"


def test_probelauf_verschluckt_keine_meldung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ein --dry-run auf der Pi darf den Hinweis-Zustand nicht speichern -- sonst gilt die
    Meldung als zugestellt und der echte Timer schickt sie nie."""
    from app.observability.ai_control import tick

    async def ohne_transport() -> None:
        return None

    monkeypatch.chdir(tmp_path)
    for name in ("DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "OPENAI_ADMIN_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tick, "current_transport", ohne_transport)
    assert tick.main(["--dry-run"]) == 0
    assert "KI-Telemetrie fehlt" in capsys.readouterr().out
    assert not ControlPaths().alert_state.exists()
