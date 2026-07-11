"""Routentests fuer das Transcribe-Limit (Operator-Review #594-Nachhaertung).

Echte Route, Whisper gemockt: exakt am Limit wird NICHT wegen Groesse
abgewiesen, ein Byte drueber ist 413 — und das Reject-Log enthaelt weder
Audiodaten noch den unbereinigten Client-Dateinamen.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.routers.kai as kai_module
from app.api.routers.kai import MAX_TRANSCRIBE_BYTES, router


def test_limit_constant_is_whisper_hard_limit() -> None:
    assert MAX_TRANSCRIBE_BYTES == 25 * 1024 * 1024


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def fake_whisper(audio_data: bytes, *, filename: str, language: str = "de") -> str:
        return "transkript"

    monkeypatch.setattr(kai_module, "transcribe_audio_via_whisper", fake_whisper)
    api = FastAPI()
    api.include_router(router)
    return TestClient(api)


def test_exactly_at_limit_is_not_size_rejected(client: TestClient) -> None:
    blob = b"x" * MAX_TRANSCRIBE_BYTES
    resp = client.post("/api/kai/transcribe", files={"audio": ("v.webm", blob, "audio/webm")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "transkript"


def test_one_byte_over_limit_is_413(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    blob = b"x" * (MAX_TRANSCRIBE_BYTES + 1)
    hostile_name = "geheim\ninjected=true.wav"
    with caplog.at_level(logging.WARNING):
        resp = client.post(
            "/api/kai/transcribe",
            files={"audio": (hostile_name, blob, "audio/wav")},
        )
    assert resp.status_code == 413
    assert resp.json()["detail"] == "audio_too_large_max_25mb"
    log_text = "\n".join(r.getMessage() for r in caplog.records if "kai-voice" in r.getMessage())
    assert f"size_bytes={MAX_TRANSCRIBE_BYTES + 1}" in log_text
    assert "audio/wav" in log_text and "'.wav'" in log_text
    # kein unbereinigter Client-Dateiname, keine Audiodaten im Log
    assert "geheim" not in log_text and "injected" not in log_text
    assert "xxxx" not in log_text
