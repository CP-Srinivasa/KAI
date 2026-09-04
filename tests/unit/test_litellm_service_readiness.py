"""Repo-only LiteLLM service readiness; never invokes systemd or a gateway."""

from pathlib import Path


def test_unit_uses_immutable_release_and_localhost_only() -> None:
    unit = Path("deploy/systemd/kai-litellm.service").read_text(encoding="utf-8")
    assert "WorkingDirectory=/home/kai/current" in unit
    assert "/home/kai/current/.venv/bin/litellm" in unit
    assert "/home/kai/current/config/litellm.yaml" in unit
    assert "--host 127.0.0.1" in unit
    assert "0.0.0.0" not in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/home/kai/ai_analyst_trading_bot" in unit


def test_config_disables_litellm_retry_and_second_telemetry() -> None:
    config = Path("config/litellm.yaml").read_text(encoding="utf-8")
    assert "num_retries: 0" in config
    assert "telemetry: false" in config
    assert "master_key: os.environ/LITELLM_MASTER_KEY" in config
    for alias in ("kai-bulk", "kai-standard", "kai-reasoning", "kai-critical", "kai-stt"):
        assert f"model_name: {alias}" in config
    assert "sk-" not in config
