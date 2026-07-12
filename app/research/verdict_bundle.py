"""Verdict-Bundle v0.1 — Generator + deterministischer Eval-Kern (Session A).

Schema + Threat Model sind VOR dieser Implementierung versiegelt
(``verifier_bundle_v0_1_schema_seal`` = ``836b1c7e28eed49a``); dieses Modul
implementiert sie, es interpretiert sie nicht neu. Der unabhängige Verifier
lebt getrennt in ``tools/verifier/kai_verify.py`` (stdlib-only) — GENERATOR
UND VERIFIER TEILEN KEINEN CODE (Threat T10): der Verifier rechnet alles
selbst nach.

Zwei Verantwortungen:

* ``build_bundle(...)`` — erzeugt ein Bundle-Verzeichnis exakt nach Schema
  v0.1 (manifest.json, preregistration.json, data_slice/, result.json,
  evidence/, reproduce.sh, reproduce.ps1, README.md).
* ``python -m app.research.verdict_bundle --eval-bundle <dir> --out <file>``
  — der EINZIGE vom Verifier whitelisted Reproduktions-Entry: liest Slice +
  Prä-Registrierung und berechnet result.json deterministisch neu (Seed aus
  prereg_id, keine Uhr, kein Netz, kein LLM).

Eval-Kind v0.1: ``canonical_stats_v1`` — n, mean, median, top_group_share,
p_positive (geseedeter Bootstrap) über ein numerisches Feld einer JSONL im
Slice, mechanisch gegen ein Gate aus Komparatoren geprüft. Bewusst klein:
genau die Statistik-Familie der bestehenden Prä-Reg-Gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.truth.attestation import canonicalize, compute_attestation

BUNDLE_SCHEMA_VERSION = "0.1"
RUNTIME_BASELINE_SHA = "1d2e565ef847efaa019e58753a65dc8f6531b0dd"
TRUTH_LINT_REGISTRY_VERSION = "11-invariants-5-active"
PROVENANCE_SEMANTICS = "pipeline_scoped"
_BOOTSTRAP_ITERATIONS = 2000

_OPS = {
    "ge": lambda a, b: a >= b,
    "gt": lambda a, b: a > b,
    "le": lambda a, b: a <= b,
    "lt": lambda a, b: a < b,
}


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    """SHA-256 über die kanonische JSON-Form (sorted keys, compact, UTF-8)."""
    return hashlib.sha256(canonicalize(payload).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Eval-Kern (deterministisch, vom Verifier reproduzierbar) ─────────────────


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def evaluate_bundle(bundle_dir: Path) -> dict[str, Any]:
    """result.json aus Slice + Prä-Registrierung DETERMINISTISCH berechnen.

    Pure gegenüber Umgebung: Seed = prereg_id, keine Uhr, kein Netz. Wirft
    ``ValueError`` bei nicht unterstütztem eval_kind (fail-closed, der
    Verifier meldet das als INCONCLUSIVE seiner Reproduktion)."""
    prereg = json.loads((bundle_dir / "preregistration.json").read_text(encoding="utf-8"))
    spec = prereg.get("bundle_eval") or {}
    if spec.get("kind") != "canonical_stats_v1":
        raise ValueError(f"unsupported eval kind: {spec.get('kind')!r}")
    slice_rel = spec.get("slice_file", "")
    if not str(slice_rel).startswith("data_slice/"):
        raise ValueError("slice_file must live under data_slice/")
    value_field = spec["value_field"]
    group_field = spec.get("group_field")
    rows = list(_iter_jsonl(bundle_dir / slice_rel))
    values: list[float] = []
    groups: dict[str, int] = {}
    for rec in rows:
        raw = rec.get(value_field)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            values.append(float(raw))
            if group_field:
                g = str(rec.get(group_field))
                groups[g] = groups.get(g, 0) + 1

    n = len(values)
    mean = statistics.fmean(values) if values else 0.0
    median = statistics.median(values) if values else 0.0
    top_group_share = (max(groups.values()) / n) if groups and n else 0.0

    # Geseedeter Bootstrap: P(mean>0). Deterministisch über prereg_id.
    rng = random.Random(prereg.get("prereg_id", "kai"))
    positive = 0
    if values:
        for _ in range(_BOOTSTRAP_ITERATIONS):
            sample = [values[rng.randrange(n)] for _ in range(n)]
            if statistics.fmean(sample) > 0:
                positive += 1
    p_positive = positive / _BOOTSTRAP_ITERATIONS if values else 0.0

    metrics: dict[str, Any] = {
        "n": n,
        "mean": round(mean, 10),
        "median": round(median, 10),
        "top_group_share": round(top_group_share, 10),
        "p_positive": round(p_positive, 10),
    }

    gate: list[dict[str, Any]] = list(spec.get("gate") or [])
    checks: list[dict[str, Any]] = []
    criteria_met = bool(gate)
    for cond in gate:
        metric, op, target = str(cond["metric"]), str(cond["op"]), float(cond["value"])
        actual = float(metrics.get(metric, 0.0))
        ok = _OPS[op](actual, target)
        criteria_met = criteria_met and ok
        checks.append({"metric": metric, "op": op, "value": target, "actual": actual, "ok": ok})

    verdict = (
        f"{'MET' if criteria_met else 'NOT_MET'} at pre-registered gate "
        f"(n={n}, {len(gate)} conditions)"
    )
    return {
        "schema": "bundle_result/v0.1",
        "eval_kind": "canonical_stats_v1",
        "prereg_id": prereg.get("prereg_id"),
        "metrics": metrics,
        "gate_checks": checks,
        "criteria_met": criteria_met,
        "verdict": verdict,
    }


# ── Generator ─────────────────────────────────────────────────────────────────


def build_bundle(
    out_dir: Path,
    *,
    preregistration: Mapping[str, Any],
    slice_files: list[tuple[str, Path]],
    code_sha: str,
    dependency_lock_source: Path,
    truth_lint_status: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
    generator_version: str = "bundle-gen/0.1",
) -> Path:
    """Bundle-Verzeichnis exakt nach versiegeltem Schema v0.1 erzeugen.

    ``preregistration`` MUSS ``prereg_id`` und ``bundle_eval`` tragen; die
    Pass-Latte steckt in der Prä-Registrierung (vor den Daten fixiert), nie
    im Generator-Aufruf."""
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "data_slice").mkdir()
    (out_dir / "evidence").mkdir()

    # Slice kopieren + Inventar pinnen.
    inputs: list[dict[str, Any]] = []
    for role, src in slice_files:
        dest_rel = f"data_slice/{src.name}"
        dest = out_dir / dest_rel
        dest.write_bytes(src.read_bytes())
        inputs.append(
            {
                "role": role,
                "path": dest_rel,
                "sha256": _file_sha256(dest),
                "bytes": dest.stat().st_size,
                "lines": sum(1 for _ in dest.open("rb")),
            }
        )

    prereg_payload = dict(preregistration)
    (out_dir / "preregistration.json").write_text(
        json.dumps(prereg_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lock_dest = out_dir / "evidence" / "dependency.lock"
    lock_dest.write_bytes(dependency_lock_source.read_bytes())

    result = evaluate_bundle(out_dir)
    (out_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest: dict[str, Any] = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "runtime_baseline_sha": RUNTIME_BASELINE_SHA,
        "truth_lint_registry_version": TRUTH_LINT_REGISTRY_VERSION,
        "provenance_semantics": PROVENANCE_SEMANTICS,
        "network_required": False,
        "llm_required": False,
        "execution_influence": False,
        "code_sha": code_sha,
        "prereg_id": str(prereg_payload.get("prereg_id", "")),
        "prereg_hash": canonical_sha256(prereg_payload),
        "inputs": inputs,
        "dependency_lock_hash": _file_sha256(lock_dest),
        "expected_verdict": {
            "verdict": result["verdict"],
            "result_sha256": canonical_sha256(result),
        },
        "truth_lint_status": dict(
            truth_lint_status
            or {
                "slice_max_severity": None,
                "preregistered_slice_warnings": [],
                "global_warnings_disclosed": [],
            }
        ),
        "generated_at_utc": (generated_at or datetime.now(UTC)).isoformat(),
        "generator_version": generator_version,
    }
    att = compute_attestation(manifest)
    manifest["attestation"] = {"algo": att["algo"], "hash": att["hash"]}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    (out_dir / "reproduce.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# Offline-Reproduktion: Repo-Checkout auf manifest.code_sha, venv aus\n"
        "# evidence/dependency.lock, dann unabhängiger Verifier mit --code-dir.\n"
        'set -euo pipefail\nBUNDLE="$(cd "$(dirname "$0")" && pwd)"\n'
        'CODE_DIR="${1:?usage: reproduce.sh <repo-checkout-at-code_sha>}"\n'
        'python "$CODE_DIR/tools/verifier/kai_verify.py" "$BUNDLE" --code-dir "$CODE_DIR"\n',
        encoding="utf-8",
        newline="\n",
    )
    (out_dir / "reproduce.ps1").write_text(
        "# Offline-Reproduktion (Windows): Repo-Checkout auf manifest.code_sha,\n"
        "# venv aus evidence/dependency.lock, dann unabhängiger Verifier.\n"
        "param([Parameter(Mandatory)][string]$CodeDir)\n"
        "$bundle = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
        'python "$CodeDir/tools/verifier/kai_verify.py" "$bundle" --code-dir "$CodeDir"\n',
        encoding="utf-8",
        newline="\n",
    )
    (out_dir / "README.md").write_text(
        "# KAI Verdict Bundle v0.1\n\n"
        "Offline verifizierbar: `python tools/verifier/kai_verify.py <dieses Verzeichnis>"
        " --code-dir <repo@code_sha>` → PASS/FAIL/INVALID/INCONCLUSIVE.\n\n"
        "Der Verifier beweist Bundle-, Prä-Reg- und Daten-Integrität sowie die"
        " Reproduktion des Ergebnisses. Er beweist NICHT, dass die Datenerhebung"
        " korrekt war, und bewertet keine Aktualität (generated_at/code_sha sind"
        " sichtbare Anker). Threat Model: docs/verifier/threat_model_v0_1.md.\n",
        encoding="utf-8",
        newline="\n",
    )
    return out_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verdict-Bundle Eval-Entry (whitelisted)")
    parser.add_argument("--eval-bundle", required=True, help="Bundle-Verzeichnis")
    parser.add_argument("--out", required=True, help="Ziel-Datei für das Ergebnis-JSON")
    args = parser.parse_args(argv)
    result = evaluate_bundle(Path(args.eval_bundle))
    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — dünner Entry
    sys.exit(main())


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "RUNTIME_BASELINE_SHA",
    "build_bundle",
    "canonical_sha256",
    "evaluate_bundle",
]
