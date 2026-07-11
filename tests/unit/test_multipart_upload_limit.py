"""Invariante F-11: Multipart-Datei-Uploads >1 MB muessen parsen.

Befund 2026-07-12: Der historische Klassen-Attr-Override in app/api/main.py ist
auf Starlette 1.x ein No-op — Uploads funktionieren trotzdem, weil der Parser
Datei-Parts nicht ueber max_part_size begrenzt. Dieser Test pinnt genau das:
faellt er je rot (Starlette begrenzt Datei-Parts wieder), ist die alte
1-MB-413-Regression zurueck und der Override muss ECHT ersetzt werden.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient


def _upload_app() -> FastAPI:
    api = FastAPI()

    @api.post("/up")
    async def up(audio: UploadFile) -> dict[str, int]:
        data = await audio.read()
        return {"size": len(data)}

    return api


@pytest.mark.parametrize("megabytes", [2, 8])
def test_multipart_over_one_megabyte_parses(megabytes: int) -> None:
    client = TestClient(_upload_app())
    blob = b"x" * (megabytes * 1024 * 1024)
    resp = client.post("/up", files={"audio": ("voice.webm", blob, "audio/webm")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["size"] == len(blob)


def test_transcribe_route_cap_stays_at_whisper_limit() -> None:
    # Die 25-MB-Grenze der Route ist das OpenAI-Whisper-Hard-Limit und bleibt.
    # Konstante statt Source-Grep (Operator-Review: refactoring-fest).
    from app.api.routers.kai import MAX_TRANSCRIBE_BYTES

    assert MAX_TRANSCRIBE_BYTES == 25 * 1024 * 1024
