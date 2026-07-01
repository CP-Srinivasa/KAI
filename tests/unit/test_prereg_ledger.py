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


def test_canonical_edge_claim_round_trips_through_the_ledger(tmp_path: Path) -> None:
    from app.research.prereg_ledger import canonical_edge_claim, canonical_edge_prereg_id

    claim = canonical_edge_claim(min_n=100, confidence=0.95)
    assert claim["name"] == "canonical_edge"
    assert claim["direction"] == "neutral"
    assert claim["horizon"] == "per_trade"
    assert claim["sample_size_target"] == 100
    assert "n>=100" in claim["success_criteria"] and "DSR>=0.95" in claim["success_criteria"]

    # The id the gate derives equals prereg_key over the shared claim.
    pid = canonical_edge_prereg_id(min_n=100, confidence=0.95)
    assert pid == prereg_key(**claim)  # type: ignore[arg-type]

    # Recording via the SAME claim makes the gate's lookup resolve — no drift
    # between what the operator registers and what edge-validation checks.
    ledger = PreRegistrationLedger(tmp_path / "prereg.jsonl")
    assert not ledger.is_registered(pid)
    ledger.record(register(**claim, created_at_utc="2026-06-30T12:00:00+00:00"))  # type: ignore[arg-type]
    assert ledger.is_registered(pid)


def test_canonical_edge_id_tracks_the_gate_bars() -> None:
    from app.research.prereg_ledger import canonical_edge_prereg_id

    base = canonical_edge_prereg_id(min_n=100, confidence=0.95)
    # A different sample floor or confidence is a different commitment → different id.
    assert canonical_edge_prereg_id(min_n=200, confidence=0.95) != base
    assert canonical_edge_prereg_id(min_n=100, confidence=0.99) != base


# ── machine-readable gate (part of the claim identity) ──────────────────────


def test_gate_changes_claim_identity_and_none_preserves_old_ids() -> None:
    from app.research.prereg_ledger import prereg_key

    base = {
        "name": "h",
        "direction": "neutral",
        "horizon": "1d",
        "success_criteria": "x",
        "sample_size_target": 100,
    }
    ungated = prereg_key(**base)
    gated = prereg_key(
        **base, gate={"level": "overall", "horizon_s": 86400, "n_min": 100, "p_min": 0.95}
    )
    stricter = prereg_key(
        **base, gate={"level": "overall", "horizon_s": 86400, "n_min": 100, "p_min": 0.99}
    )
    assert ungated != gated
    assert gated != stricter  # any threshold change = a different claim
    assert prereg_key(**base) == ungated  # gate=None keeps free-text-era ids


def test_registration_round_trips_gate_through_json() -> None:
    import json as _json

    from app.research.prereg_ledger import PreRegistration, register

    gate = {
        "level": "pooled",
        "horizon_s": 86400,
        "n_min": 300,
        "p_min": 0.95,
        "i2_max": 0.5,
        "k_min": 8,
    }
    entry = register(
        name="h",
        direction="neutral",
        horizon="1d",
        success_criteria="x",
        sample_size_target=300,
        created_at_utc="2026-07-02T00:00:00+00:00",
        gate=gate,
    )
    loaded = PreRegistration.from_dict(_json.loads(entry.to_json()))
    assert loaded.gate == gate
    assert loaded.prereg_id == entry.prereg_id
    # legacy rows without the field stay loadable
    legacy = _json.loads(entry.to_json())
    legacy.pop("gate")
    assert PreRegistration.from_dict(legacy).gate is None
