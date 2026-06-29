"""Pre-registration ledger tests."""

from __future__ import annotations

from pathlib import Path

from app.research.prereg_ledger import (
    DEFAULT_PREREG_LEDGER_PATH,
    PreRegistration,
    PreRegistrationLedger,
    prereg_key,
    register,
)


def _key(**over: object) -> str:
    base: dict[str, object] = {
        "name": "funding_carry_long",
        "direction": "long",
        "horizon": "24h",
        "success_criteria": "net_mean_bps>0 at n>=200, DSR>=0.95",
        "sample_size_target": 200,
    }
    base.update(over)
    return prereg_key(**base)  # type: ignore[arg-type]


def test_key_is_deterministic_and_normalised() -> None:
    a = _key()
    assert len(a) == 16
    # case + whitespace variants collapse to the same identity
    assert _key(name="Funding_Carry_Long", direction="LONG") == a
    assert _key(success_criteria="  net_mean_bps>0   at  n>=200,  DSR>=0.95 ") == a


def test_key_changes_with_config() -> None:
    base = _key()
    assert _key(name="basis_short") != base
    assert _key(direction="short") != base
    assert _key(horizon="4h") != base
    assert _key(success_criteria="net>0 only") != base
    assert _key(sample_size_target=100) != base


def _reg(name: str = "funding_carry_long", direction: str = "long") -> PreRegistration:
    return register(
        name=name,
        direction=direction,
        horizon="24h",
        success_criteria="net_mean_bps>0 at n>=200, DSR>=0.95",
        sample_size_target=200,
        created_at_utc="2026-06-29T20:00:00+00:00",
    )


def test_register_stamps_matching_id_and_normalises() -> None:
    entry = register(
        name="  Funding Carry  ",
        direction="LONG",
        horizon="24h",
        success_criteria="net>0   at  n>=200",
        sample_size_target=200,
        created_at_utc="2026-06-29T20:00:00+00:00",
    )
    assert entry.direction == "long"
    assert entry.name == "Funding Carry"
    assert entry.success_criteria == "net>0 at n>=200"
    assert entry.prereg_id == prereg_key(
        name="Funding Carry",
        direction="long",
        horizon="24h",
        success_criteria="net>0 at n>=200",
        sample_size_target=200,
    )
    assert entry.schema == "prereg/v1"


def test_record_and_read_roundtrip(tmp_path: Path) -> None:
    ledger = PreRegistrationLedger(tmp_path / "prereg.jsonl")
    entry = _reg()
    ledger.record(entry)
    entries = ledger.entries()
    assert len(entries) == 1
    assert entries[0].prereg_id == entry.prereg_id
    assert entries[0].sample_size_target == 200
    assert entries[0].created_at_utc == "2026-06-29T20:00:00+00:00"


def test_is_registered_and_count_distinctness(tmp_path: Path) -> None:
    ledger = PreRegistrationLedger(tmp_path / "prereg.jsonl")
    a, b = _reg(name="a"), _reg(name="b")
    ledger.record(a)
    ledger.record(b)
    ledger.record(_reg(name="a"))  # identical claim re-registered (new row)
    assert ledger.is_registered(a.prereg_id)
    assert ledger.is_registered(b.prereg_id)
    assert not ledger.is_registered(_reg(name="never").prereg_id)
    assert ledger.count() == 2  # distinct claims, not rows
    assert len(ledger.entries()) == 3


def test_missing_file_is_empty(tmp_path: Path) -> None:
    ledger = PreRegistrationLedger(tmp_path / "nope.jsonl")
    assert ledger.entries() == []
    assert ledger.count() == 0


def test_corrupt_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "prereg.jsonl"
    ledger = PreRegistrationLedger(path)
    ledger.record(_reg())
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
        fh.write("\n")
    assert len(ledger.entries()) == 1


def test_default_path_under_artifacts_research() -> None:
    assert DEFAULT_PREREG_LEDGER_PATH.as_posix() == "artifacts/research/prereg_ledger.jsonl"
