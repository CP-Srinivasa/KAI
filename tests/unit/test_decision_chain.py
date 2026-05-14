"""Decision-Journal Hash-Chain Tests (Adaptive-Learning Schritt 2).

Deckt:
- Genesis-prev-hash bei erstem Entry
- prev-hash-Kette zwischen sequenziellen Entries
- chain_hash-Konsistenz (recompute matches stored)
- Tamper-Detection: modifizierter chain_hash → error
- Tamper-Detection: modifizierter record_hash → error
- Record-Hash-Cross-Check mit journal_records
- Korrupte Lines werden geskippt
- Duplikat-Detection
"""

from __future__ import annotations

import json
from pathlib import Path

from app.audit.decision_chain import (
    CHAIN_SCHEMA_VERSION,
    GENESIS_PREV_HASH,
    _hash_chain_entry,
    append_chain_entry,
    hash_record,
    last_chain_hash,
    load_journal_records_for_verify,
    verify_chain,
)


def _sample_record(decision_id: str = "dec_aaa111") -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "symbol": "BTC/USDT",
        "venue": "paper",
        "confidence_score": 0.65,
        "thesis": "Test thesis",
    }


class TestHashRecord:
    def test_deterministic(self) -> None:
        r = _sample_record()
        assert hash_record(r) == hash_record(r)

    def test_different_for_different_records(self) -> None:
        assert hash_record(_sample_record("dec_a")) != hash_record(_sample_record("dec_b"))

    def test_key_order_independent(self) -> None:
        r1 = {"a": 1, "b": 2, "c": 3}
        r2 = {"c": 3, "b": 2, "a": 1}
        assert hash_record(r1) == hash_record(r2)


class TestAppendChainEntry:
    def test_genesis_first_entry(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        entry = append_chain_entry(
            chain_path=chain,
            decision_id="dec_001",
            record_payload=_sample_record("dec_001"),
        )
        assert entry.prev_chain_hash == GENESIS_PREV_HASH
        assert entry.decision_id == "dec_001"
        assert len(entry.chain_hash) == 64
        assert len(entry.record_hash) == 64
        assert entry.schema_version == CHAIN_SCHEMA_VERSION

    def test_chain_continues(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        e1 = append_chain_entry(
            chain_path=chain,
            decision_id="dec_001",
            record_payload=_sample_record("dec_001"),
        )
        e2 = append_chain_entry(
            chain_path=chain,
            decision_id="dec_002",
            record_payload=_sample_record("dec_002"),
        )
        assert e2.prev_chain_hash == e1.chain_hash
        assert e2.chain_hash != e1.chain_hash

    def test_file_layout_is_jsonl(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        append_chain_entry(
            chain_path=chain,
            decision_id="dec_001",
            record_payload=_sample_record("dec_001"),
        )
        append_chain_entry(
            chain_path=chain,
            decision_id="dec_002",
            record_payload=_sample_record("dec_002"),
        )
        lines = chain.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # parses without raising


class TestLastChainHash:
    def test_missing_file_returns_genesis(self, tmp_path: Path) -> None:
        assert last_chain_hash(tmp_path / "missing.jsonl") == GENESIS_PREV_HASH

    def test_returns_last_entry_chain_hash(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        append_chain_entry(
            chain_path=chain,
            decision_id="dec_001",
            record_payload=_sample_record("dec_001"),
        )
        e2 = append_chain_entry(
            chain_path=chain,
            decision_id="dec_002",
            record_payload=_sample_record("dec_002"),
        )
        assert last_chain_hash(chain) == e2.chain_hash

    def test_skips_corrupt_lines(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        append_chain_entry(
            chain_path=chain,
            decision_id="dec_001",
            record_payload=_sample_record("dec_001"),
        )
        with chain.open("a") as fh:
            fh.write("not json\n")
        e2 = append_chain_entry(
            chain_path=chain,
            decision_id="dec_002",
            record_payload=_sample_record("dec_002"),
        )
        # last_chain_hash sollte e2's chain_hash sein, korrupte Line ignoriert
        assert last_chain_hash(chain) == e2.chain_hash


class TestVerifyChain:
    def test_empty_chain_no_errors(self, tmp_path: Path) -> None:
        assert verify_chain(chain_path=tmp_path / "missing.jsonl") == []

    def test_single_entry_genesis_ok(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        append_chain_entry(
            chain_path=chain,
            decision_id="dec_001",
            record_payload=_sample_record("dec_001"),
        )
        assert verify_chain(chain_path=chain) == []

    def test_three_entries_ok(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        for did in ("a", "b", "c"):
            append_chain_entry(
                chain_path=chain,
                decision_id=did,
                record_payload=_sample_record(did),
            )
        assert verify_chain(chain_path=chain) == []

    def test_tampered_chain_hash_detected(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        append_chain_entry(
            chain_path=chain,
            decision_id="dec_001",
            record_payload=_sample_record("dec_001"),
        )
        # Tamper: chain_hash modifizieren
        line = chain.read_text().strip()
        data = json.loads(line)
        data["chain_hash"] = "f" * 64
        chain.write_text(json.dumps(data) + "\n")

        errors = verify_chain(chain_path=chain)
        assert any("chain_hash_mismatch" in e for e in errors)

    def test_broken_prev_link_detected(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        append_chain_entry(
            chain_path=chain,
            decision_id="dec_001",
            record_payload=_sample_record("dec_001"),
        )
        # 2. Entry mit gefälschtem prev_chain_hash via direct-write
        bogus = {
            "schema_version": 1,
            "decision_id": "dec_002",
            "record_hash": "a" * 64,
            "prev_chain_hash": "9" * 64,  # bogus
            "chained_at_utc": "2026-05-11T13:00:00+00:00",
        }
        bogus_chain_hash = _hash_chain_entry(bogus)
        bogus["chain_hash"] = bogus_chain_hash
        with chain.open("a") as fh:
            fh.write(json.dumps(bogus) + "\n")

        errors = verify_chain(chain_path=chain)
        # chain_break (prev_chain_hash mismatch)
        assert any("chain_break" in e for e in errors)

    def test_record_hash_cross_check_ok(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        rec = _sample_record("dec_001")
        append_chain_entry(
            chain_path=chain,
            decision_id="dec_001",
            record_payload=rec,
        )
        journal = {"dec_001": rec}
        assert verify_chain(chain_path=chain, journal_records=journal) == []

    def test_record_hash_cross_check_tamper(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        rec = _sample_record("dec_001")
        append_chain_entry(
            chain_path=chain,
            decision_id="dec_001",
            record_payload=rec,
        )
        # Operator/Attacker hat das Journal-File modifiziert nach dem Append
        tampered = dict(rec)
        tampered["confidence_score"] = 0.99
        journal = {"dec_001": tampered}
        errors = verify_chain(chain_path=chain, journal_records=journal)
        assert any("record_hash_mismatch" in e for e in errors)

    def test_missing_journal_record_detected(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        append_chain_entry(
            chain_path=chain,
            decision_id="dec_lost",
            record_payload=_sample_record("dec_lost"),
        )
        # Journal-Records leer (Operator hat Datei gelöscht?)
        errors = verify_chain(chain_path=chain, journal_records={})
        assert any("missing_journal_record" in e for e in errors)

    def test_duplicate_decision_id_detected(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.jsonl"
        # Zweimal mit selber ID — sollte als duplicate erkannt werden
        for _ in range(2):
            append_chain_entry(
                chain_path=chain,
                decision_id="dec_dup",
                record_payload=_sample_record("dec_dup"),
            )
        errors = verify_chain(chain_path=chain)
        assert any("duplicate_chain_entry" in e for e in errors)


class TestLoadJournalRecords:
    def test_missing_file_empty(self, tmp_path: Path) -> None:
        assert load_journal_records_for_verify(tmp_path / "missing.jsonl") == {}

    def test_loads_decision_ids(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.jsonl"
        records = [_sample_record("dec_001"), _sample_record("dec_002")]
        jp.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        loaded = load_journal_records_for_verify(jp)
        assert set(loaded.keys()) == {"dec_001", "dec_002"}
        assert loaded["dec_001"]["confidence_score"] == 0.65

    def test_skips_corrupt_lines(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.jsonl"
        jp.write_text(
            json.dumps(_sample_record("dec_001"))
            + "\n"
            + "garbage\n"
            + json.dumps(_sample_record("dec_002"))
            + "\n"
        )
        loaded = load_journal_records_for_verify(jp)
        assert set(loaded.keys()) == {"dec_001", "dec_002"}
