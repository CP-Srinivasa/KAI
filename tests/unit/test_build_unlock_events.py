"""Tests for scripts.build_unlock_events — schema-2 output with generated_at."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from scripts import build_unlock_events as bue


def _fake_doc() -> dict:
    return {
        "metadata": {
            "unlockEvents": [
                {"timestamp": 1_700_000_000, "cliffAllocations": [{"amount": 100.0}]},
                {"timestamp": 1_700_100_000, "linearAllocations": [{"amount": 50.0}]},
            ]
        },
        "supplyMetrics": {"maxSupply": 1000.0},
    }


def test_build_writes_schema2_with_generated_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bue, "_fetch", lambda slug, timeout: _fake_doc())
    out = tmp_path / "unlock_events.json"

    rc = bue.build({"aptos": "APT"}, out, timeout=1.0)

    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == 2
    # generated_at must be a parseable UTC ISO timestamp (honest staleness anchor).
    parsed = datetime.fromisoformat(doc["generated_at"])
    assert parsed.tzinfo is not None
    # token payload still matches the schema-1 shape consumers rely on.
    apt = doc["tokens"]["APT"]
    assert apt["max_supply"] == 1000.0
    assert apt["events"] == [[1_700_000_000_000, 100.0], [1_700_100_000_000, 50.0]]


def test_build_skips_failed_fetch_but_still_stamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A dead source (None) is skipped; the artifact is still written with a
    # timestamp so the dashboard can age it rather than crash.
    monkeypatch.setattr(bue, "_fetch", lambda slug, timeout: None)
    out = tmp_path / "unlock_events.json"

    rc = bue.build({"aptos": "APT"}, out, timeout=1.0)

    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == 2
    assert doc["tokens"] == {}
    assert isinstance(doc["generated_at"], str)
