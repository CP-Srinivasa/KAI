#!/usr/bin/env python3
"""kai_verify — unabhängiger Offline-Verifier für KAI Verdict-Bundles v0.1.

STDLIB-ONLY, bewusst klein und lesbar (Threat T10): ein Prüfer muss diesem
Skript nicht vertrauen — er kann jeden Schritt von Hand nachrechnen (kanonische
JSON-Form = sorted keys, compact separators, UTF-8; SHA-256). Teilt KEINEN Code
mit dem Generator. Öffnet niemals das Netz (T12). Folgt keinen Symlinks (T3).

Sieben Schritte in versiegelter Reihenfolge, Abbruch beim ersten INVALID
(Threat Model ``docs/verifier/threat_model_v0_1.md``, Seal ``836b1c7e28eed49a``):

  1. Manifest gegen Schema v0.1 (inkl. Konstanten)
  2. Attestation-Rehash des Manifests
  3. Input-Inventar vollständig + alle Datei-Hashes (Größen VOR dem Lesen, T9)
  4. prereg_hash gegen preregistration.json
  5. Slice-Lint: resolved Rows brauchen beweisbare Provenance (T7/T8)
  6. Verdikt reproduzieren (whitelisted Entry im Repo-Checkout bei code_sha)
  7. erwartetes ↔ reproduziertes Ergebnis vergleichen

Ausgabe: Prüfschritt-Liste + GENAU EIN Statuswort als letzte Zeile.
Exit-Codes: PASS=0 · FAIL=1 · INVALID=2 · INCONCLUSIVE=3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT = {"PASS": 0, "FAIL": 1, "INVALID": 2, "INCONCLUSIVE": 3}

_SCHEMA_CONSTANTS = {
    "bundle_schema_version": "0.1",
    "runtime_baseline_sha": "1d2e565ef847efaa019e58753a65dc8f6531b0dd",
    "truth_lint_registry_version": "11-invariants-5-active",
    "provenance_semantics": "pipeline_scoped",
    "network_required": False,
    "llm_required": False,
    "execution_influence": False,
}
_REQUIRED_FIELDS = (
    *_SCHEMA_CONSTANTS,
    "code_sha",
    "prereg_id",
    "prereg_hash",
    "inputs",
    "dependency_lock_hash",
    "expected_verdict",
    "truth_lint_status",
    "generated_at_utc",
    "attestation",
)
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_SLICE_PATH = re.compile(r"^data_slice/[A-Za-z0-9._/-]+$")
_TOTAL_BYTES_CAP = 512 * 1024 * 1024  # T9: harte Gesamtgrenze
# Der EINZIGE erlaubte Reproduktions-Entry — kommt aus dem Repo-Checkout
# (verankert über code_sha), niemals aus dem Bundle (T10: kein Bundle-Code-Exec).
_REPRO_ENTRY = ("-m", "app.research.verdict_bundle")


def _canonical_sha256(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Check:
    """Sammelt Prüfschritt-Zeilen; wirft _Verdict beim ersten harten Verstoß."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def ok(self, step: str, detail: str = "") -> None:
        self.lines.append(f"  [ok] {step}" + (f" — {detail}" if detail else ""))

    def invalid(self, step: str, reason: str) -> None:
        self.lines.append(f"  [INVALID] {step} — {reason}")
        raise _VerdictError("INVALID")

    def inconclusive(self, step: str, reason: str) -> None:
        self.lines.append(f"  [INCONCLUSIVE] {step} — {reason}")
        raise _VerdictError("INCONCLUSIVE")


class _VerdictError(Exception):
    def __init__(self, status: str) -> None:
        self.status = status


def _inside(base: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _step1_schema(c: Check, manifest: object) -> dict:
    if not isinstance(manifest, dict):
        c.invalid("1/7 Schema", "Manifest ist kein Objekt")
    assert isinstance(manifest, dict)
    for field in _REQUIRED_FIELDS:
        if field not in manifest:
            c.invalid("1/7 Schema", f"Pflichtfeld fehlt: {field}")
    extra = set(manifest) - set(_REQUIRED_FIELDS) - {"generator_version"}
    if extra:
        c.invalid("1/7 Schema", f"unbekannte Felder: {sorted(extra)}")
    for field, expected in _SCHEMA_CONSTANTS.items():
        if manifest[field] != expected:
            c.invalid("1/7 Schema", f"Konstante verletzt: {field}={manifest[field]!r}")
    if not (isinstance(manifest["code_sha"], str) and _HEX40.match(manifest["code_sha"])):
        c.invalid("1/7 Schema", "code_sha kein 40-hex Git-SHA")
    if not (isinstance(manifest["prereg_id"], str) and _HEX16.match(manifest["prereg_id"])):
        c.invalid("1/7 Schema", "prereg_id kein 16-hex")
    for hex_field in ("prereg_hash", "dependency_lock_hash"):
        if not (isinstance(manifest[hex_field], str) and _HEX64.match(manifest[hex_field])):
            c.invalid("1/7 Schema", f"{hex_field} kein sha256-hex")
    inputs = manifest["inputs"]
    if not (isinstance(inputs, list) and inputs):
        c.invalid("1/7 Schema", "inputs leer")
    for i, item in enumerate(inputs):
        if not isinstance(item, dict):
            c.invalid("1/7 Schema", f"inputs[{i}] kein Objekt")
        for req in ("role", "path", "sha256", "bytes"):
            if req not in item:
                c.invalid("1/7 Schema", f"inputs[{i}].{req} fehlt")
        if not _SLICE_PATH.match(str(item["path"])) or ".." in str(item["path"]):
            c.invalid("1/7 Schema", f"inputs[{i}].path unzulässig: {item['path']!r}")
        if not (isinstance(item["bytes"], int) and item["bytes"] >= 0):
            c.invalid("1/7 Schema", f"inputs[{i}].bytes unzulässig")
        if not _HEX64.match(str(item["sha256"])):
            c.invalid("1/7 Schema", f"inputs[{i}].sha256 kein sha256-hex")
    ev = manifest["expected_verdict"]
    if not (
        isinstance(ev, dict)
        and isinstance(ev.get("verdict"), str)
        and _HEX64.match(str(ev.get("result_sha256", "")))
    ):
        c.invalid("1/7 Schema", "expected_verdict unvollständig")
    tls = manifest["truth_lint_status"]
    if not isinstance(tls, dict) or tls.get("slice_max_severity") not in (None, "INFO", "WARNING"):
        c.invalid(
            "1/7 Schema",
            "slice_max_severity unzulässig (ERROR/CRITICAL im Slice = kein gültiges Bundle)",
        )
    if not isinstance(tls.get("preregistered_slice_warnings"), list) or not isinstance(
        tls.get("global_warnings_disclosed"), list
    ):
        c.invalid("1/7 Schema", "truth_lint_status-Listen fehlen")
    att = manifest["attestation"]
    if not (
        isinstance(att, dict)
        and att.get("algo") == "sha256"
        and _HEX64.match(str(att.get("hash", "")))
    ):
        c.invalid("1/7 Schema", "attestation unzulässig")
    c.ok("1/7 Schema v0.1", "alle Pflichtfelder + Konstanten")
    return manifest


def _step2_attestation(c: Check, manifest: dict) -> None:
    body = {k: v for k, v in manifest.items() if k != "attestation"}
    actual = _canonical_sha256(body)
    if actual != manifest["attestation"]["hash"]:
        c.invalid("2/7 Attestation", "Manifest-Rehash weicht ab (T1: nachträglich editiert?)")
    c.ok("2/7 Attestation", actual[:16] + "…")


def _step3_inventory(c: Check, bundle: Path, manifest: dict) -> None:
    slice_dir = bundle / "data_slice"
    if not slice_dir.is_dir():
        c.invalid("3/7 Inventar", "data_slice/ fehlt")
    declared = {str(item["path"]): item for item in manifest["inputs"]}
    total_declared = sum(int(item["bytes"]) for item in declared.values())
    if total_declared > _TOTAL_BYTES_CAP:
        c.invalid("3/7 Inventar", f"Gesamtgröße {total_declared} über Kappe (T9)")
    actual_files: set[str] = set()
    for path in sorted(slice_dir.rglob("*")):
        if path.is_symlink():
            c.invalid("3/7 Inventar", f"Symlink im Slice: {path.name} (T3)")
        if path.is_dir():
            continue
        rel = path.relative_to(bundle).as_posix()
        actual_files.add(rel)
    if actual_files != set(declared):
        missing = sorted(set(declared) - actual_files)
        stray = sorted(actual_files - set(declared))
        c.invalid("3/7 Inventar", f"Inventar-Drift (T2) — fehlt: {missing}, fremd: {stray}")
    for rel, item in sorted(declared.items()):
        target = bundle / rel
        if not _inside(bundle, target):
            c.invalid("3/7 Inventar", f"Pfad verlässt Bundle: {rel} (T3)")
        size = target.stat().st_size
        if size != int(item["bytes"]):
            c.invalid("3/7 Inventar", f"{rel}: Größe {size} ≠ deklariert {item['bytes']} (T9)")
        digest = _file_sha256(target)
        if digest != item["sha256"]:
            c.invalid("3/7 Inventar", f"{rel}: Hash weicht ab (T2)")
    c.ok("3/7 Inventar", f"{len(declared)} Datei(en) vollständig + hash-identisch")


def _step4_prereg(c: Check, bundle: Path, manifest: dict) -> dict:
    path = bundle / "preregistration.json"
    if not path.is_file():
        c.invalid("4/7 Prä-Registrierung", "preregistration.json fehlt")
    try:
        prereg = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        c.invalid("4/7 Prä-Registrierung", "kein gültiges JSON")
    actual = _canonical_sha256(prereg)
    if actual != manifest["prereg_hash"]:
        c.invalid("4/7 Prä-Registrierung", "prereg_hash weicht ab (T4: Pass-Latte getauscht?)")
    if str(prereg.get("prereg_id", "")) != manifest["prereg_id"]:
        c.invalid("4/7 Prä-Registrierung", "prereg_id im Dokument ≠ Manifest")
    c.ok("4/7 Prä-Registrierung", actual[:16] + "…")
    return prereg


def _step5_slice_lint(c: Check, bundle: Path, manifest: dict) -> None:
    """Unabhängige Slice-Prüfung: resolved Outcome-Rows OHNE beweisbare
    Provenance (signal_path_id) machen das Bundle INVALID (T7). WARNINGs sind
    nur zulässig, wenn im Manifest prä-registriert offengelegt (T8)."""
    violations = 0
    for item in manifest["inputs"]:
        target = bundle / str(item["path"])
        if not target.name.endswith(".jsonl"):
            continue
        with target.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("outcome") not in ("hit", "miss"):
                    continue
                prov = rec.get("provenance")
                pid = prov.get("signal_path_id") if isinstance(prov, dict) else None
                if not (isinstance(pid, str) and pid):
                    violations += 1
    if violations:
        c.invalid("5/7 Slice-Lint", f"{violations} resolved Row(s) ohne beweisbare Provenance (T7)")
    tls = manifest["truth_lint_status"]
    if tls["slice_max_severity"] == "WARNING" and not tls["preregistered_slice_warnings"]:
        c.invalid("5/7 Slice-Lint", "WARNING im Slice ohne prä-registrierte Offenlegung (T8)")
    c.ok("5/7 Slice-Lint", "Slice provenance-sauber")


def _step6_reproduce(c: Check, bundle: Path, manifest: dict, code_dir: Path | None) -> dict:
    if code_dir is None:
        c.inconclusive(
            "6/7 Reproduktion",
            "kein --code-dir — Integrität 1-5 geprüft, Ergebnis nicht reproduziert",
        )
    assert code_dir is not None
    if not code_dir.is_dir():
        c.inconclusive("6/7 Reproduktion", f"code-dir fehlt: {code_dir}")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=code_dir,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001 — Umgebung nicht herstellbar
        c.inconclusive("6/7 Reproduktion", f"git HEAD nicht lesbar ({exc})")
        raise AssertionError from exc  # unreachable
    if head != manifest["code_sha"]:
        c.inconclusive(
            "6/7 Reproduktion",
            f"HEAD {head[:12]} != code_sha {manifest['code_sha'][:12]} — Checkout herstellen",
        )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "reproduced_result.json"
        proc = subprocess.run(
            [sys.executable, *_REPRO_ENTRY, "--eval-bundle", str(bundle), "--out", str(out)],
            cwd=code_dir,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            c.inconclusive(
                "6/7 Reproduktion",
                f"Eval-Entry Exit {proc.returncode}: {proc.stderr.strip()[:160]}",
            )
        try:
            reproduced = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            c.inconclusive("6/7 Reproduktion", "reproduziertes Ergebnis nicht lesbar")
    c.ok("6/7 Reproduktion", "deterministischer Re-Lauf abgeschlossen")
    return reproduced


def _step7_compare(c: Check, bundle: Path, manifest: dict, reproduced: dict) -> str:
    expected_sha = manifest["expected_verdict"]["result_sha256"]
    try:
        shipped = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        c.invalid("7/7 Vergleich", "result.json fehlt/unlesbar")
        raise AssertionError from None  # unreachable
    if _canonical_sha256(shipped) != expected_sha:
        c.invalid("7/7 Vergleich", "result.json ≠ versiegelter result_sha256 (T5)")
    repro_sha = _canonical_sha256(reproduced)
    if repro_sha != expected_sha:
        c.invalid("7/7 Vergleich", "Reproduktion weicht vom versiegelten Ergebnis ab (T5)")
    c.ok("7/7 Vergleich", "erwartet = ausgeliefert = reproduziert")
    return "PASS" if reproduced.get("criteria_met") is True else "FAIL"


def verify(bundle: Path, code_dir: Path | None) -> tuple[str, list[str]]:
    c = Check()
    try:
        try:
            manifest_raw = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            c.invalid("1/7 Schema", "manifest.json fehlt/unlesbar")
            raise AssertionError from None  # unreachable
        manifest = _step1_schema(c, manifest_raw)
        _step2_attestation(c, manifest)
        _step3_inventory(c, bundle, manifest)
        _step4_prereg(c, bundle, manifest)
        _step5_slice_lint(c, bundle, manifest)
        reproduced = _step6_reproduce(c, bundle, manifest, code_dir)
        status = _step7_compare(c, bundle, manifest, reproduced)
        return status, c.lines
    except _VerdictError as v:
        return v.status, c.lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KAI Verdict-Bundle Offline-Verifier v0.1")
    parser.add_argument("bundle", help="Pfad zum Bundle-Verzeichnis")
    parser.add_argument(
        "--code-dir",
        default=None,
        help="Repo-Checkout bei manifest.code_sha (für Reproduktion); ohne → INCONCLUSIVE",
    )
    args = parser.parse_args(argv)
    status, lines = verify(Path(args.bundle), Path(args.code_dir) if args.code_dir else None)
    for line in lines:
        print(line)
    print(status)
    return EXIT[status]


if __name__ == "__main__":  # pragma: no cover — dünner Entry
    sys.exit(main())
