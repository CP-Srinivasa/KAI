from __future__ import annotations

import tomllib
from pathlib import Path

from app.ingestion.youtube import adapter


class _Snippet:
    def __init__(self, text: str) -> None:
        self.text = text


class _Transcript:
    def fetch(self) -> list[_Snippet]:
        return [_Snippet("hello"), _Snippet("world")]


class _TranscriptList:
    def find_transcript(self, languages: list[str]) -> _Transcript:
        assert languages == ["en"]
        return _Transcript()


class _TranscriptApi:
    calls: list[str] = []

    def list(self, video_id: str) -> _TranscriptList:
        self.calls.append(video_id)
        return _TranscriptList()


def test_fetch_transcript_uses_current_instance_api(monkeypatch) -> None:
    monkeypatch.setattr(adapter, "YouTubeTranscriptApi", _TranscriptApi)

    assert adapter.fetch_transcript("vid-1") == "hello world"
    assert _TranscriptApi.calls == ["vid-1"]


def test_mypy_override_ratchet_keeps_youtube_adapter_strict() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    override_modules = pyproject["tool"]["mypy"]["overrides"][0]["module"]

    assert len(override_modules) <= 5
    assert "app.ingestion.youtube.adapter" not in override_modules
