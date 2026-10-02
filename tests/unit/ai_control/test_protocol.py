from datetime import UTC, datetime
from pathlib import Path

from app.observability.ai_control import protocol
from app.observability.ai_control.config import ControlPaths

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def test_nur_sichere_schalter_ohne_geheimnisse(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        'KAI_INFERENCE_ROUTE_MODES={"standard":"primary"}\n'
        "KAI_INFERENCE_LITELLM_API_KEY=sk-geheim\n"
        "OPENAI_API_KEY=sk-x\nSOURCE_LLM_SPARFENSTER_MODE=off\n# kommentar\n"
    )
    werte = protocol.safe_switches(env)
    assert werte == {
        "KAI_INFERENCE_ROUTE_MODES": '{"standard":"primary"}',
        "SOURCE_LLM_SPARFENSTER_MODE": "off",
    }


def test_aenderung_landet_im_protokoll_begrenzt(tmp_path: Path) -> None:
    paths = ControlPaths(
        env_file=tmp_path / ".env", protocol=tmp_path / "rt" / "p.json", runtime_dir=tmp_path / "rt"
    )
    paths.env_file.write_text("SOURCE_LLM_SPARFENSTER_MODE=off\n")
    assert protocol.record(paths, now=NOW) == []  # erster Lauf = Basis
    paths.env_file.write_text("SOURCE_LLM_SPARFENSTER_MODE=enforce\n")
    neu = protocol.record(paths, now=NOW, extra={"RELEASE": "7446c486"})
    schluessel = {a["key"]: a for a in neu}
    assert schluessel["SOURCE_LLM_SPARFENSTER_MODE"]["new"] == "enforce"
    assert schluessel["RELEASE"]["new"] == "7446c486"
    assert protocol.read(paths.protocol)[0]["ts"] == NOW.isoformat()
