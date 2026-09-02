from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_litellm_config_uses_env_references_and_no_nested_retries() -> None:
    config = yaml.safe_load((ROOT / "deploy/litellm/config.yaml").read_text("utf-8"))
    assert config["litellm_settings"]["num_retries"] == 0
    assert config["litellm_settings"]["telemetry"] is False
    aliases = {entry["model_name"] for entry in config["model_list"]}
    assert {"kai-bulk", "kai-standard", "kai-reasoning", "kai-critical", "kai-stt"} <= aliases
    for entry in config["model_list"]:
        assert entry["litellm_params"]["api_key"].startswith("os.environ/")
    assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


def test_systemd_gateway_is_loopback_unprivileged_and_not_server_dependency() -> None:
    service = (ROOT / "deploy/systemd/kai-litellm.service").read_text("utf-8")
    assert "User=ubuntu" in service
    assert "--host 127.0.0.1" in service
    assert "EnvironmentFile=/etc/kai/litellm.env" in service
    assert "NoNewPrivileges=true" in service
    assert "CapabilityBoundingSet=" in service
    assert "LITELLM_MASTER_KEY=" not in service
    server = (ROOT / "deploy/systemd/kai-server.service").read_text("utf-8")
    assert "kai-litellm.service" not in server


def test_litellm_environment_example_contains_names_only() -> None:
    lines = (ROOT / "deploy/litellm/litellm.env.example").read_text("utf-8").splitlines()
    assignments = [line for line in lines if line and not line.startswith("#")]
    assert assignments
    assert all(line.endswith("=") for line in assignments)
