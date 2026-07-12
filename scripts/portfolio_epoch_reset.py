"""Attestierter Portfolio-Epochenwechsel — Weg B+ (Operator-Direktive 2026-07-12).

Schreibt genau EIN append-only ``portfolio_epoch_reset``-Event in das Paper-
Audit-Log und startet damit Epoche ``paper_v2_attested`` bei 10.000 USD. Die
Legacy-Historie wird NICHT veraendert: sie bleibt vollstaendig im Log, wird
zusaetzlich unveraendert archiviert (SHA-256-attestiert) und ist ab der
Epochengrenze INVALID_FOR_PERFORMANCE. Offene Legacy-Positionen werden NICHT
in die neue Epoche uebernommen und NICHT mit erfundenen Markt-Exits
geschlossen — sie werden im Event als ``invalidated_at_epoch_boundary``
dokumentiert (performance_effect_new_epoch=0).

Referenz: Memory kai_paper_epoch_reset_directive_20260712 +
kai_paper_equity_contamination_20260712 (Befund-Forensik).

Sicherheit:
  - Dry-run per Default; ``--apply`` ist fuer den Schreibvorgang Pflicht.
  - ``--operator-approved`` ist Pflicht (der Entscheid liegt beim Operator).
  - Verweigert ohne aktives Buch-Freeze (EXECUTION_PAPER_FROZEN=true), denn
    der Reset ERST nach Ursachen-Schliessung + eingefrorenem Buch erfolgen darf.
  - Idempotent: existiert bereits ein Event mit derselben new_epoch_id, bricht
    der Lauf ab (genau EIN Epochenwechsel-Event).
  - Append-only unter demselben File-Lock wie der Engine-Writer.

    python -m scripts.portfolio_epoch_reset --forensic-report <pfad>            # dry-run
    python -m scripts.portfolio_epoch_reset --forensic-report <pfad> \
        --operator-approved --apply                                             # schreiben
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.audit.stream_validation import PaperExecutionAuditStreamRow
from app.core.file_lock import append_lock
from app.core.settings import get_settings
from app.execution.audit_replay import last_epoch_reset_info, replay_paper_audit

_AUDIT = Path("artifacts/paper_execution_audit.jsonl")
_ARCHIVE_DIR = Path("artifacts/archive")

OLD_EPOCH_ID = "legacy_contaminated"
NEW_EPOCH_ID = "paper_v2_attested"
RESET_REASON = "historical_accounting_contamination"
NEW_STARTING_CASH_USD = 10_000.0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_lines(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _resolve_code_sha(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-path", type=Path, default=_AUDIT)
    parser.add_argument("--archive-dir", type=Path, default=_ARCHIVE_DIR)
    parser.add_argument(
        "--forensic-report",
        type=Path,
        required=True,
        help="Pfad zum Forensik-/Befund-Report (wird SHA-256-gehasht und im Event referenziert)",
    )
    parser.add_argument("--new-cash", type=float, default=NEW_STARTING_CASH_USD)
    parser.add_argument("--code-sha", default=None, help="Deployed Fix-SHA (Default: git HEAD)")
    parser.add_argument(
        "--operator-approved",
        action="store_true",
        help="Pflicht fuer --apply: bestaetigt den vorliegenden Operator-Entscheid (Weg B+)",
    )
    parser.add_argument("--apply", action="store_true", help="wirklich schreiben (sonst dry-run)")
    args = parser.parse_args(argv)

    audit_path: Path = args.audit_path
    if not audit_path.exists():
        print(f"ABORT: audit file missing: {audit_path}")
        return 2
    if not args.forensic_report.exists():
        print(f"ABORT: forensic report missing: {args.forensic_report}")
        return 2
    if args.new_cash <= 0:
        print("ABORT: --new-cash must be > 0")
        return 2

    # Genau EIN Epochenwechsel: existiert das Event schon, ist jeder weitere
    # Lauf ein Fehler (kein zweiter Reset ohne neue Operator-Direktive).
    existing = last_epoch_reset_info(audit_path)
    if existing is not None and existing[0] == NEW_EPOCH_ID:
        print(f"ABORT: epoch event already present ({existing[0]} at {existing[1]})")
        return 3

    # Buch-Freeze ist Eingangsbedingung fuer den Vollzug (Direktive Schritt 1+4).
    frozen = get_settings().execution.paper_frozen
    if args.apply and not frozen:
        print("ABORT: EXECUTION_PAPER_FROZEN is not true - freeze the book first")
        return 4
    if args.apply and not args.operator_approved:
        print("ABORT: --operator-approved is required for --apply")
        return 4

    replay = replay_paper_audit(audit_path)
    if not replay.available:
        print(f"ABORT: legacy replay failed ({replay.error}) - book state unverified")
        return 5

    invalidated_positions = [
        {
            "symbol": pos.symbol,
            "quantity": pos.quantity,
            "avg_entry_price": pos.avg_entry_price,
            "position_side": pos.position_side,
            "source": pos.source,
            "opened_at": pos.opened_at,
            "legacy_position_status": "invalidated_at_epoch_boundary",
            "performance_effect_new_epoch": 0.0,
        }
        for pos in sorted(replay.positions.values(), key=lambda p: p.symbol)
    ]

    legacy_sha = _sha256_file(audit_path)
    legacy_lines = _count_lines(audit_path)
    forensic_sha = _sha256_file(args.forensic_report)
    code_sha = _resolve_code_sha(args.code_sha)
    now_utc = datetime.now(UTC).isoformat()

    archive_name = f"paper_execution_audit_{OLD_EPOCH_ID}_{now_utc[:19].replace(':', '')}.jsonl"
    archive_path = args.archive_dir / archive_name

    event = {
        "schema_version": "v2",
        "event_type": "portfolio_epoch_reset",
        "timestamp_utc": now_utc,
        "old_epoch_id": OLD_EPOCH_ID,
        "new_epoch_id": NEW_EPOCH_ID,
        "reason": RESET_REASON,
        "old_book_performance_valid": False,
        "old_track_record_status": "INVALID_FOR_PERFORMANCE",
        "new_starting_cash_usd": args.new_cash,
        "legacy_book_sha256": legacy_sha,
        "legacy_book_lines": legacy_lines,
        "legacy_book_archive_path": str(archive_path),
        "legacy_cash_usd_at_boundary": round(replay.cash_usd, 8),
        "legacy_realized_pnl_usd_at_boundary": round(replay.realized_pnl_usd, 8),
        "forensic_report_path": str(args.forensic_report),
        "forensic_report_sha256": forensic_sha,
        "code_sha": code_sha,
        "operator_approved": bool(args.operator_approved),
        "operator_directive": "kai_paper_epoch_reset_directive_20260712",
        "invalidated_positions": invalidated_positions,
    }
    # Schema-Validierung mit demselben Modell, das der Engine-Writer nutzt.
    PaperExecutionAuditStreamRow.model_validate(event)

    if not args.apply:
        print("DRY-RUN (kein Write). Event-Vorschau:")
        print(json.dumps(event, indent=2, ensure_ascii=True))
        return 0

    # 1) Altes Buch unveraendert archivieren + Hash attestieren.
    args.archive_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(audit_path, archive_path)
    archived_sha = _sha256_file(archive_path)
    if archived_sha != legacy_sha:
        print("ABORT: archive copy hash mismatch - book changed mid-run, retry under freeze")
        try:
            archive_path.unlink()
        except OSError:
            pass
        return 6
    (archive_path.with_suffix(".jsonl.sha256")).write_text(
        f"{legacy_sha}  {archive_path.name}\n", encoding="utf-8"
    )

    # 2) Genau EIN append-only Epochen-Event unter dem Engine-File-Lock.
    with append_lock(audit_path):
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=True) + "\n")

    # 3) Vollzug verifizieren: Replay muss die neue Epoche liefern.
    verify = replay_paper_audit(audit_path)
    ok = (
        verify.available
        and verify.epoch_id == NEW_EPOCH_ID
        and not verify.positions
        and abs(verify.cash_usd - args.new_cash) < 1e-9
        and verify.realized_pnl_usd == 0.0
    )
    attestation = {
        "applied": True,
        "verified": ok,
        "event": event,
        "archive_sha256_verified": True,
        "post_reset_state": {
            "epoch_id": verify.epoch_id,
            "cash_usd": verify.cash_usd,
            "open_positions": len(verify.positions),
            "realized_pnl_usd": verify.realized_pnl_usd,
        },
    }
    print(json.dumps(attestation, indent=2, ensure_ascii=True))
    return 0 if ok else 7


if __name__ == "__main__":
    sys.exit(main())
