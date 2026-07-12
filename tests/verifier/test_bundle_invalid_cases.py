"""Verdict-Bundle v0.1 — INVALID-/Manipulationsfälle ZUERST (Threat Model T1-T12).

Jeder Test baut ein ECHTES, gültiges Bundle über den Generator und korrumpiert
dann genau EINEN Aspekt — der unabhängige Verifier muss den versiegelten
Statuswert liefern (PASS=0 · FAIL=1 · INVALID=2 · INCONCLUSIVE=3). Der Verifier
wird als eigenständiges Modul geladen (tools/, kein app-Import in ihm).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "kai_verify", _REPO / "tools" / "verifier" / "kai_verify.py"
)
kai_verify = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(kai_verify)

from app.research.verdict_bundle import build_bundle, canonical_sha256  # noqa: E402


def _repo_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=_REPO, capture_output=True, text=True, check=True
    ).stdout.strip()


def _slice_rows(with_provenance: bool = True, values: list[float] | None = None) -> list[dict]:
    rows = []
    for i, v in enumerate(values or [12.0, 8.0, 15.0, 9.0, 11.0]):
        rec: dict = {
            "outcome": "hit" if v > 0 else "miss",
            "asset": "BTC/USDT",
            "net_bps": v,
            "document_id": f"doc-{i}",
        }
        if with_provenance:
            rec["provenance"] = {"signal_path_id": "rsspath_news_v1"}
        rows.append(rec)
    return rows


def _make_bundle(
    tmp_path: Path,
    *,
    gate_met: bool = True,
    with_provenance: bool = True,
    code_sha: str | None = None,
) -> Path:
    slice_file = tmp_path / "outcomes.jsonl"
    slice_file.write_text(
        "".join(json.dumps(r) + "\n" for r in _slice_rows(with_provenance)), encoding="utf-8"
    )
    lock = tmp_path / "dependency.lock"
    lock.write_text("pinned==1.0\n", encoding="utf-8")
    prereg = {
        "prereg_id": "abcdef0123456789",
        "name": "bundle_smoke_claim",
        "success_criteria": "mean net_bps >= 5 at n>=5" if gate_met else "mean net_bps >= 50",
        "bundle_eval": {
            "kind": "canonical_stats_v1",
            "slice_file": "data_slice/outcomes.jsonl",
            "value_field": "net_bps",
            "group_field": "asset",
            "gate": [
                {"metric": "n", "op": "ge", "value": 5},
                {"metric": "mean", "op": "ge", "value": 5 if gate_met else 50},
            ],
        },
    }
    return build_bundle(
        tmp_path / "bundle",
        preregistration=prereg,
        slice_files=[("outcomes", slice_file)],
        code_sha=code_sha or _repo_head(),
        dependency_lock_source=lock,
        generated_at=datetime(2026, 7, 12, 14, 0, 0, tzinfo=UTC),
    )


def _verify(bundle: Path, *, with_code_dir: bool = True) -> tuple[str, list[str]]:
    return kai_verify.verify(bundle, _REPO if with_code_dir else None)


def _rewrite_manifest(bundle: Path, mutate) -> None:
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    mutate(manifest)
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ── Happy path zuerst als Anker (PASS + FAIL sind beide "gesund") ─────────────


def test_valid_bundle_reproduces_to_pass(tmp_path: Path) -> None:
    status, lines = _verify(_make_bundle(tmp_path))
    assert status == "PASS", lines
    assert kai_verify.EXIT[status] == 0


def test_unmet_gate_is_honest_fail_not_invalid(tmp_path: Path) -> None:
    # FAIL = Kriterium verfehlt bei intaktem Bundle — ehrliches Negativ.
    status, lines = _verify(_make_bundle(tmp_path, gate_met=False))
    assert status == "FAIL", lines
    assert kai_verify.EXIT[status] == 1


def test_missing_code_dir_is_inconclusive(tmp_path: Path) -> None:
    status, _ = _verify(_make_bundle(tmp_path), with_code_dir=False)
    assert status == "INCONCLUSIVE"
    assert kai_verify.EXIT[status] == 3


# ── T1: Manifest nachträglich editiert ────────────────────────────────────────


def test_t1_tampered_expected_verdict_is_invalid(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    _rewrite_manifest(
        bundle, lambda m: m["expected_verdict"].__setitem__("verdict", "MET, ehrlich!")
    )
    status, lines = _verify(bundle)
    assert status == "INVALID"
    assert any("2/7" in ln and "INVALID" in ln for ln in lines)


# ── T2: Slice manipuliert / Datei fremd / Datei fehlt ─────────────────────────


def test_t2_modified_slice_file_is_invalid(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    target = bundle / "data_slice" / "outcomes.jsonl"
    target.write_text(
        target.read_text(encoding="utf-8") + '{"outcome":"hit","net_bps":999}\n', encoding="utf-8"
    )
    status, lines = _verify(bundle)
    assert status == "INVALID"
    assert any("3/7" in ln and "INVALID" in ln for ln in lines)


def test_t2_stray_file_in_slice_is_invalid(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    (bundle / "data_slice" / "eingeschmuggelt.jsonl").write_text("{}\n", encoding="utf-8")
    status, _ = _verify(bundle)
    assert status == "INVALID"


def test_t2_deleted_slice_file_is_invalid(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    (bundle / "data_slice" / "outcomes.jsonl").unlink()
    status, _ = _verify(bundle)
    assert status == "INVALID"


# ── T3: Pfad-Traversal im Inventar ────────────────────────────────────────────


def test_t3_traversal_path_in_inputs_is_invalid(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)

    def mutate(m: dict) -> None:
        m["inputs"][0]["path"] = "data_slice/../../etc/passwd"

    _rewrite_manifest(bundle, mutate)
    status, lines = _verify(bundle)
    assert status == "INVALID"
    assert any("1/7" in ln and "INVALID" in ln for ln in lines)


# ── T4: Prä-Registrierung ausgetauscht ────────────────────────────────────────


def test_t4_swapped_preregistration_is_invalid(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    prereg = json.loads((bundle / "preregistration.json").read_text(encoding="utf-8"))
    prereg["bundle_eval"]["gate"][1]["value"] = 0  # weichere Pass-Latte
    (bundle / "preregistration.json").write_text(json.dumps(prereg, indent=2), encoding="utf-8")
    status, lines = _verify(bundle)
    assert status == "INVALID"
    assert any("4/7" in ln and "INVALID" in ln for ln in lines)


# ── T5: result.json „verbessert" ─────────────────────────────────────────────


def test_t5_tampered_result_is_invalid(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path, gate_met=False)
    result = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
    result["criteria_met"] = True  # aus NOT_MET wird "MET"
    (bundle / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    status, lines = _verify(bundle)
    assert status == "INVALID"
    assert any("7/7" in ln and "INVALID" in ln for ln in lines)


# ── T7: Slice-Row ohne beweisbare Provenance ─────────────────────────────────


def test_t7_slice_row_without_provenance_is_invalid(tmp_path: Path) -> None:
    status, lines = _verify(_make_bundle(tmp_path, with_provenance=False))
    assert status == "INVALID"
    assert any("5/7" in ln and "INVALID" in ln for ln in lines)


# ── T8: WARNING im Slice ohne prä-registrierte Offenlegung ───────────────────


def test_t8_undisclosed_slice_warning_is_invalid(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)

    def mutate(m: dict) -> None:
        m["truth_lint_status"]["slice_max_severity"] = "WARNING"
        m["truth_lint_status"]["preregistered_slice_warnings"] = []
        body = {k: v for k, v in m.items() if k != "attestation"}
        m["attestation"] = {"algo": "sha256", "hash": canonical_sha256(body)}

    _rewrite_manifest(bundle, mutate)
    status, lines = _verify(bundle)
    assert status == "INVALID"
    assert any("5/7" in ln and "INVALID" in ln for ln in lines)


# ── T9: Größen-Manipulation ──────────────────────────────────────────────────


def test_t9_byte_size_mismatch_is_invalid(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)

    def mutate(m: dict) -> None:
        m["inputs"][0]["bytes"] = 1
        body = {k: v for k, v in m.items() if k != "attestation"}
        m["attestation"] = {"algo": "sha256", "hash": canonical_sha256(body)}

    _rewrite_manifest(bundle, mutate)
    status, _ = _verify(bundle)
    assert status == "INVALID"


# ── T12: Netz/LLM/Execution in den Beweisweg geschmuggelt ────────────────────


@pytest.mark.parametrize("flag", ["network_required", "llm_required", "execution_influence"])
def test_t12_forbidden_capability_flags_are_invalid(tmp_path: Path, flag: str) -> None:
    bundle = _make_bundle(tmp_path)

    def mutate(m: dict) -> None:
        m[flag] = True
        body = {k: v for k, v in m.items() if k != "attestation"}
        m["attestation"] = {"algo": "sha256", "hash": canonical_sha256(body)}

    _rewrite_manifest(bundle, mutate)
    status, lines = _verify(bundle)
    assert status == "INVALID"
    assert any("1/7" in ln and "INVALID" in ln for ln in lines)


# ── Determinismus: zwei Re-Läufe, ein Ergebnis ───────────────────────────────


def test_reproduction_is_deterministic_across_runs(tmp_path: Path) -> None:
    from app.research.verdict_bundle import evaluate_bundle

    bundle = _make_bundle(tmp_path)
    a = evaluate_bundle(bundle)
    b = evaluate_bundle(bundle)
    assert canonical_sha256(a) == canonical_sha256(b)
