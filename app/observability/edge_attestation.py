"""Externally-recomputable attestation for the canonical-edge report (B5b).

The canonical-edge attestation (``trading canonical-edge --attest``) seals a report
into the hash-chained truth ledger. On its own that proves the CLAIM was not edited
after the fact — but not that a third party can reproduce the numbers. B5b closes
that gap by pinning, INTO the attested payload:

  * ``inputs`` — every audit artifact the window builder read, as
    ``{role, path, sha256, lines}`` (deterministically sorted). The input STATE is
    now part of the sealed claim.
  * ``recompute`` — the CLI knobs that shaped the numbers (``min_sample``,
    ``p_threshold_bps``, the ``until`` bound) so the window is reproducible.
  * ``code`` — the git HEAD commit + dirty flag at attest time (fail-soft: ``None``
    when git is unavailable).

:func:`verify_canonical_edge_seq` turns third-party verification into ONE command:
read ledger entry ``<seq>``, re-hash the pinned input prefixes (append-only growth
is fine; shrink / prefix change fails loud), reconstruct the window from those
prefixes, reassemble the payload and compare the SHA-256. Legacy pre-B5b entries
(no ``inputs``) fall back to a plain payload-hash recomputation with a clear note.

Read-only: no execution, no capital, no mutation of the audit streams.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.edge_report import MIN_SAMPLE_FOR_P
from app.observability.evidence_window import (
    CANONICAL_EDGE_SOURCES,
    EvidenceWindowReport,
    build_window_from_lines,
)
from app.truth.attestation import compute_attestation
from app.truth.input_pin import pin_inputs, read_lines, verify_input_pin
from app.truth.ledger import DEFAULT_TRUTH_LEDGER_PATH, read_record

CANONICAL_EDGE_KIND = "canonical_edge_report"
LOOP_ROLE = "loop_audit"
EXEC_ROLE = "exec_audit"
_GIT_TIMEOUT_S = 10


def git_code_state(repo_dir: str | Path | None = None) -> dict[str, Any] | None:
    """git HEAD commit hash + working-tree dirty flag, or ``None`` (fail-soft).

    ``{"commit": <full-sha>, "dirty": <bool>}`` when git is available; ``None`` when
    git is missing, this is not a repo, or the call errors — a code stamp must never
    crash an attestation (the Pi has git; a bare verifier host may not).
    """
    cwd = str(repo_dir) if repo_dir is not None else None
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
        if head.returncode != 0:
            return None
        commit = head.stdout.strip()
        if not commit:
            return None
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else False
    except (OSError, subprocess.SubprocessError):
        return None
    return {"commit": commit, "dirty": dirty}


#: Sentinel: „dieser Aufruf sagt nichts ueber die Konfiguration".
#:
#: Braucht es, weil ``_assemble_payload`` BEIDE Wege bedient. Wuerde beim
#: Verifizieren einer Alt-Attestierung ein ``config``-Schluessel entstehen, den
#: sie nie hatte, aendert sich ihr Payload — und jede historische Zeile fiele
#: mit „hash mismatch" durch. Der Schluessel entsteht deshalb nur, wenn er
#: uebergeben wird; beim Verifizieren wird der GESIEGELTE Wert durchgereicht.
_CONFIG_ABSENT = object()

#: Repo-verwaltete Konfiguration, die in eine Messung eingeht.
#: **Nicht** dabei: ``.env``. Geheimnisse gehoeren in kein Payload, auch nicht
#: als Hash — ein Hash ueber eine kurze, ratbare Menge ist kein Schutz.
CONFIG_GLOBS: tuple[str, ...] = ("config/*.yaml", "config/*.json")


def config_state(repo_dir: str | Path | None = None) -> dict[str, Any] | None:
    """Hash der repo-verwalteten Konfiguration, oder ``None`` (fail-soft).

    **Der Befund (R2-15).** Attestierungen pinnen heute die Eingaben und den
    Code — die Konfiguration nirgends. Ein versiegelter Report ist damit nicht
    reproduzierbar: dieselben Zeilen, derselbe Commit, andere Schwellen in
    ``config/`` ergeben eine andere Zahl, und nichts im Payload sagt es.

    Liefert ``{"files": {relpath: sha256}, "config_sha256": <rollup>}``.
    Der Rollup ist der SHA-256 ueber die kanonische Form der Dateiliste, damit
    ein Verifizierer EINEN Wert vergleichen kann statt acht.

    Fail-soft wie ``git_code_state``: fehlt das Verzeichnis, ist der Stempel
    ``None`` — eine Attestierung darf an ihrem eigenen Stempel nicht scheitern.
    """
    root = Path(repo_dir) if repo_dir is not None else Path.cwd()
    try:
        files: dict[str, str] = {}
        for pattern in CONFIG_GLOBS:
            for path in sorted(root.glob(pattern)):
                if not path.is_file():
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                files[path.relative_to(root).as_posix()] = digest
    except OSError:
        return None
    if not files:
        return None
    rollup = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"files": files, "config_sha256": rollup}


def _assemble_payload(
    report: EvidenceWindowReport,
    inputs: list[dict[str, Any]],
    code: dict[str, Any] | None,
    *,
    min_sample: int,
    p_threshold_bps: float,
    until: str | None,
    config: Any = _CONFIG_ABSENT,
) -> dict[str, Any]:
    """Build the attested payload from parts. ONE assembler for attest AND verify.

    Using the identical function on both paths guarantees the reconstructed payload
    can never structurally drift from what was sealed.
    """
    payload: dict[str, Any] = dict(report.to_dict())
    payload["inputs"] = inputs
    payload["recompute"] = {
        "min_sample": int(min_sample),
        "p_threshold_bps": float(p_threshold_bps),
        "until": until,
    }
    payload["code"] = code
    # Nur setzen, wenn der Aufrufer etwas zu sagen hat (siehe _CONFIG_ABSENT).
    if config is not _CONFIG_ABSENT:
        payload["config"] = config
    return payload


def build_canonical_edge_payload(
    *,
    loop_audit_path: str | Path,
    exec_audit_path: str | Path,
    since: datetime | None = None,
    until: datetime | None = None,
    min_sample: int = MIN_SAMPLE_FOR_P,
    p_threshold_bps: float = 0.0,
    repo_dir: str | Path | None = None,
    root: Path | None = None,
) -> tuple[EvidenceWindowReport, dict[str, Any]]:
    """Build the canonical-edge report AND its input/code-pinned attestation payload.

    Reads each audit file EXACTLY once and both pins and builds from those same
    lines (no time-of-check/time-of-use gap). ``until`` bounds the window end and is
    recorded in the payload so verification replays the identical bound.
    """
    loop_lines = read_lines(loop_audit_path)
    exec_lines = read_lines(exec_audit_path)
    inputs = pin_inputs(
        [
            (LOOP_ROLE, loop_audit_path, loop_lines),
            (EXEC_ROLE, exec_audit_path, exec_lines),
        ],
        root=root,
    )
    report = build_window_from_lines(
        loop_lines=loop_lines,
        exec_lines=exec_lines,
        since=since,
        until=until,
        min_sample=min_sample,
        p_threshold_bps=p_threshold_bps,
        source_allowlist=CANONICAL_EDGE_SOURCES,
    )
    payload = _assemble_payload(
        report,
        inputs,
        git_code_state(repo_dir),
        min_sample=min_sample,
        p_threshold_bps=p_threshold_bps,
        until=until.isoformat() if until is not None else None,
        config=config_state(repo_dir),
    )
    return report, payload


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of a ``--verify <seq>`` run — carries the operator message + exit sense."""

    ok: bool
    seq: int
    message: str
    reason: str = ""


def _parse_iso(value: Any) -> datetime | None:
    """Tolerant ISO-8601 -> tz-aware UTC datetime; ``None`` on any parse failure."""
    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


def verify_canonical_edge_seq(
    seq: int,
    *,
    ledger_path: str | Path = DEFAULT_TRUTH_LEDGER_PATH,
    root: Path | None = None,
) -> VerifyResult:
    """Recompute attested ledger entry ``<seq>`` from its pinned inputs (fail-closed).

    Returns ``VERIFY OK`` only when the pinned input prefixes still match AND the
    payload reassembled from the reconstructed report re-hashes to the sealed
    ``payload_hash``. Legacy entries without an ``inputs`` section fall back to a
    plain payload-hash recomputation, flagged so the operator knows inputs were not
    pinned. Any other outcome is a clear ``VERIFY FAIL``.
    """
    record = read_record(seq, path=Path(ledger_path))
    if record is None:
        return VerifyResult(
            False, seq, f"VERIFY FAIL seq={seq}: not found in ledger", "seq_not_found"
        )
    payload = record.get("payload")
    stored_hash = record.get("payload_hash")
    if not isinstance(payload, dict) or not isinstance(stored_hash, str):
        return VerifyResult(
            False, seq, f"VERIFY FAIL seq={seq}: malformed ledger record", "malformed_record"
        )

    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        return _verify_legacy(seq, payload, stored_hash)

    return _verify_pinned(seq, payload, stored_hash, inputs, root=root)


def _verify_legacy(seq: int, payload: dict[str, Any], stored_hash: str) -> VerifyResult:
    """Pre-B5b entry: no pinned inputs -> recompute the payload hash only."""
    recomputed = compute_attestation(payload)["hash"]
    if recomputed == stored_hash:
        return VerifyResult(
            True,
            seq,
            f"VERIFY OK seq={seq} (inputs nicht gepinnt (pre-B5b); nur Payload-Hash rekomputiert)",
            "legacy_no_inputs",
        )
    return VerifyResult(
        False,
        seq,
        f"VERIFY FAIL seq={seq}: payload_hash mismatch (pre-B5b, inputs nicht gepinnt)",
        "legacy_hash_mismatch",
    )


def _verify_pinned(
    seq: int,
    payload: dict[str, Any],
    stored_hash: str,
    inputs: list[Any],
    *,
    root: Path | None,
) -> VerifyResult:
    """B5b entry: verify pinned prefixes, reconstruct the window, re-hash the payload."""
    prefixes: dict[str, list[str]] = {}
    for pin in inputs:
        if not isinstance(pin, dict):
            return VerifyResult(
                False, seq, f"VERIFY FAIL seq={seq}: malformed input pin", "malformed_pin"
            )
        check = verify_input_pin(pin, root=root)
        if not check.ok:
            return VerifyResult(
                False, seq, f"VERIFY FAIL seq={seq}: {check.reason}", "input_pin_mismatch"
            )
        prefixes[str(pin.get("role", ""))] = check.prefix_lines

    if LOOP_ROLE not in prefixes or EXEC_ROLE not in prefixes:
        return VerifyResult(
            False, seq, f"VERIFY FAIL seq={seq}: missing pinned role (loop/exec)", "missing_role"
        )

    recompute = payload.get("recompute")
    recompute = recompute if isinstance(recompute, dict) else {}
    try:
        min_sample = int(recompute.get("min_sample", MIN_SAMPLE_FOR_P))
        p_threshold_bps = float(recompute.get("p_threshold_bps", 0.0))
    except (TypeError, ValueError):
        return VerifyResult(
            False, seq, f"VERIFY FAIL seq={seq}: malformed recompute params", "malformed_recompute"
        )
    until_raw = recompute.get("until")
    until = _parse_iso(until_raw)

    report = build_window_from_lines(
        loop_lines=prefixes[LOOP_ROLE],
        exec_lines=prefixes[EXEC_ROLE],
        until=until,
        min_sample=min_sample,
        p_threshold_bps=p_threshold_bps,
        source_allowlist=CANONICAL_EDGE_SOURCES,
    )
    # Reassemble via the SAME assembler using the STORED metadata (inputs already
    # independently re-hashed above; code is attested metadata carried through).
    recomputed_payload = _assemble_payload(
        report,
        list(inputs),
        payload.get("code"),
        min_sample=min_sample,
        p_threshold_bps=p_threshold_bps,
        until=until_raw if isinstance(until_raw, str) else None,
        # Durchreichen, nicht neu bilden: die Konfiguration von HEUTE hat mit
        # der versiegelten Zeile nichts zu tun. Und war kein config-Schluessel
        # versiegelt, darf auch keiner entstehen.
        config=payload["config"] if "config" in payload else _CONFIG_ABSENT,
    )
    recomputed_hash = compute_attestation(recomputed_payload)["hash"]
    if recomputed_hash == stored_hash:
        return VerifyResult(True, seq, f"VERIFY OK seq={seq}", "ok")

    # Ein Mismatch hat ZWEI sehr verschiedene Ursachen, und sie zu vermengen
    # macht die Verifikation unbrauchbar:
    #
    #   (a) das Siegel ist gebrochen              -> echter Befund
    #   (b) die Zeile ist mit ANDEREM Code gesiegelt worden und laesst sich mit
    #       dem heutigen nicht mehr nachrechnen   -> Aussage ueber uns, nicht
    #                                                ueber die Zeile
    #
    # Live gemessen am 2026-09-01 auf dem Pi: von 64 canonical-edge-Zeilen
    # verifizieren 17, 47 nicht — und KEIN gesiegelter Commit kommt in beiden
    # Mengen vor. Die Trennlinie ist exakt der Code-Stand, nicht die Zeile.
    #
    # Beide bleiben ``ok=False`` — nachgerechnet ist nachgerechnet, und ein
    # nicht reproduzierbarer Beweis ist kein Beweis. Aber der Grund sagt jetzt,
    # WAS zu tun ist: Siegel pruefen oder mit dem gesiegelten Commit nachrechnen.
    sealed_commit = ""
    code_block = payload.get("code")
    if isinstance(code_block, dict):
        sealed_commit = str(code_block.get("commit") or "")
    current = git_code_state(root)
    current_commit = str(current.get("commit") or "") if isinstance(current, dict) else ""
    if sealed_commit and current_commit and sealed_commit != current_commit:
        return VerifyResult(
            False,
            seq,
            f"VERIFY FAIL seq={seq}: nicht nachrechenbar mit dem heutigen Code "
            f"(gesiegelt unter {sealed_commit[:8]}, geprueft unter {current_commit[:8]}). "
            "Die Eingaben-Pins stimmen; der Payload wurde von anderem Code gebildet. "
            "Zum Nachweis mit dem gesiegelten Commit nachrechnen.",
            "code_version_drift",
        )
    return VerifyResult(
        False,
        seq,
        f"VERIFY FAIL seq={seq}: recomputed payload hash mismatch "
        f"(sealed {stored_hash[:16]}..., recomputed {recomputed_hash[:16]}...)",
        "hash_mismatch",
    )


__all__ = [
    "CANONICAL_EDGE_KIND",
    "CONFIG_GLOBS",
    "EXEC_ROLE",
    "LOOP_ROLE",
    "VerifyResult",
    "build_canonical_edge_payload",
    "config_state",
    "git_code_state",
    "verify_canonical_edge_seq",
]
