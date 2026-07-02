"""L3 OpenTimestamps stamper: the calendar commitment MUST be merged into the
proof (else the .ots proves nothing / cannot upgrade to a Bitcoin proof), and a
total calendar outage must fail honestly. Calendars are mocked — no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.integrity.anchor import AnchorUnavailableError, OpenTimestampsStamper


def test_ots_stamper_merges_calendar_commitment(tmp_path: Path, monkeypatch) -> None:
    ts_mod = pytest.importorskip("opentimestamps.core.timestamp")
    from opentimestamps.calendar import RemoteCalendar
    from opentimestamps.core.notary import PendingAttestation

    timestamp_cls = ts_mod.Timestamp
    calls = {"submit": 0, "merged": 0}

    def fake_submit(self, digest, timeout=None):  # type: ignore[no-untyped-def]
        calls["submit"] += 1
        # Mimic a real calendar response: a Timestamp carrying a pending
        # attestation, so the merged proof is non-empty (and serializable).
        t = timestamp_cls(digest)
        t.attestations.add(PendingAttestation("https://fake.calendar.test"))
        return t

    orig_merge = timestamp_cls.merge

    def spy_merge(self, other):  # type: ignore[no-untyped-def]
        calls["merged"] += 1
        return orig_merge(self, other)

    monkeypatch.setattr(RemoteCalendar, "submit", fake_submit)
    monkeypatch.setattr(timestamp_cls, "merge", spy_merge)

    proof = OpenTimestampsStamper().stamp("ab" * 32, tmp_path)
    assert Path(proof).exists() and Path(proof).stat().st_size > 0
    assert calls["submit"] >= 1
    assert calls["merged"] >= 1  # the fix: calendar commitment merged into the proof


def test_ots_stamper_raises_when_no_calendar_commits(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("opentimestamps")
    from opentimestamps.calendar import RemoteCalendar

    def boom(self, digest, timeout=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("calendar down")

    monkeypatch.setattr(RemoteCalendar, "submit", boom)
    with pytest.raises(AnchorUnavailableError):
        OpenTimestampsStamper().stamp("cd" * 32, tmp_path)
