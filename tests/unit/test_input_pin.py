"""Input-pinning primitives (B5b) — deterministic seal + append-only prefix verify.

These pin the behaviour that makes a canonical-edge attestation recomputable by a
third party: the pin is deterministic, sorted, CRLF/LF-stable, tolerant to
append-only growth, and fails loud on a shrunk file or a changed pinned prefix.
"""

from __future__ import annotations

from pathlib import Path

from app.truth.input_pin import (
    hash_lines,
    pin_input,
    pin_inputs,
    read_lines,
    verify_input_pin,
)


def test_pin_input_carries_role_path_sha_lines(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    lines = ["one", "two", "three"]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    pin = pin_input("exec_audit", f, read_lines(f), root=tmp_path)
    assert pin["role"] == "exec_audit"
    assert pin["path"] == "a.jsonl"
    assert pin["lines"] == 3
    assert pin["sha256"] == hash_lines(lines)


def test_pin_is_deterministic_regardless_of_call() -> None:
    a = pin_input("r", "x/y.jsonl", ["l1", "l2"], root=Path("x"))
    b = pin_input("r", "x/y.jsonl", ["l1", "l2"], root=Path("x"))
    assert a == b


def test_pin_inputs_sorted_by_role_then_path() -> None:
    pins = pin_inputs(
        [
            ("loop_audit", "loop.jsonl", ["a"]),
            ("exec_audit", "exec.jsonl", ["b"]),
        ],
        root=Path("."),
    )
    assert [p["role"] for p in pins] == ["exec_audit", "loop_audit"]


def test_hash_lines_is_crlf_lf_stable(tmp_path: Path) -> None:
    lf = tmp_path / "lf.jsonl"
    crlf = tmp_path / "crlf.jsonl"
    lf.write_bytes(b"a\nb\n")
    crlf.write_bytes(b"a\r\nb\r\n")
    assert read_lines(lf) == read_lines(crlf) == ["a", "b"]
    assert hash_lines(read_lines(lf)) == hash_lines(read_lines(crlf)) == hash_lines(["a", "b"])


def test_read_lines_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_lines(tmp_path / "nope.jsonl") == []


def test_verify_ok_on_unchanged_file(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    f.write_text("x\ny\n", encoding="utf-8")
    pin = pin_input("r", f, read_lines(f), root=tmp_path)
    check = verify_input_pin(pin, root=tmp_path)
    assert check.ok
    assert check.prefix_lines == ["x", "y"]


def test_verify_ok_after_append_only_growth(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    f.write_text("x\ny\n", encoding="utf-8")
    pin = pin_input("r", f, read_lines(f), root=tmp_path)
    with f.open("a", encoding="utf-8") as fh:
        fh.write("z\nw\n")  # append-only growth
    check = verify_input_pin(pin, root=tmp_path)
    assert check.ok
    # only the pinned prefix is returned for reconstruction, not the new lines
    assert check.prefix_lines == ["x", "y"]


def test_verify_fail_on_shrink(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    f.write_text("x\ny\nz\n", encoding="utf-8")
    pin = pin_input("r", f, read_lines(f), root=tmp_path)
    f.write_text("x\n", encoding="utf-8")  # shrank below pinned line count
    check = verify_input_pin(pin, root=tmp_path)
    assert not check.ok
    assert "shrank" in check.reason


def test_verify_fail_on_changed_pinned_prefix(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    f.write_text("x\ny\n", encoding="utf-8")
    pin = pin_input("r", f, read_lines(f), root=tmp_path)
    f.write_text("x\nTAMPERED\n", encoding="utf-8")  # a pinned line changed
    check = verify_input_pin(pin, root=tmp_path)
    assert not check.ok
    assert "prefix changed" in check.reason


def test_verify_fail_on_missing_file(tmp_path: Path) -> None:
    pin = {"role": "r", "path": "gone.jsonl", "sha256": "0" * 64, "lines": 2}
    check = verify_input_pin(pin, root=tmp_path)
    assert not check.ok
    assert "missing" in check.reason


def test_verify_fail_on_malformed_lines_field(tmp_path: Path) -> None:
    pin = {"role": "r", "path": "x.jsonl", "sha256": "0" * 64, "lines": None}
    check = verify_input_pin(pin, root=tmp_path)
    assert not check.ok
    assert "malformed" in check.reason


def test_empty_file_pins_and_verifies(tmp_path: Path) -> None:
    f = tmp_path / "empty.jsonl"
    f.write_text("", encoding="utf-8")
    pin = pin_input("r", f, read_lines(f), root=tmp_path)
    assert pin["lines"] == 0
    assert verify_input_pin(pin, root=tmp_path).ok
