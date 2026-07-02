"""Operator CLI: truth attestation, compliance export, capital snapshot (ADR 0013).

Registers on the existing ``trading`` sub-app (imported at the bottom of
``trading.py``) so the god-file ``app/cli/main.py`` stays untouched. Everything
here is read/append-only tooling — no order, no capital movement, no gate is
weakened.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer

from app.cli.commands.trading import trading_app


@trading_app.command("truth-attest-prereg")
def truth_attest_prereg(
    prereg_path: str | None = typer.Option(
        None, "--prereg-path", help="Prereg-Ledger JSONL (default: kanonischer Pfad)"
    ),
    ledger_path: str | None = typer.Option(
        None, "--ledger-path", help="Truth-Attestation-Ledger JSONL (default: kanonischer Pfad)"
    ),
    audit_mirror: bool = typer.Option(
        True, "--audit-mirror/--no-audit-mirror", help="Attestation in den KAI-Audit spiegeln"
    ),
) -> None:
    """Attestiert alle noch nicht attestierten Prä-Registrierungen (idempotent)."""
    from app.truth.ledger import DEFAULT_TRUTH_LEDGER_PATH, attest_prereg_ledger

    result = attest_prereg_ledger(
        prereg_path=Path(prereg_path) if prereg_path else None,
        truth_path=Path(ledger_path) if ledger_path else DEFAULT_TRUTH_LEDGER_PATH,
        mirror_audit=audit_mirror,
    )
    print(json.dumps(result, indent=2))


@trading_app.command("truth-verify")
def truth_verify(
    ledger_path: str | None = typer.Option(
        None, "--ledger-path", help="Truth-Attestation-Ledger JSONL (default: kanonischer Pfad)"
    ),
) -> None:
    """Verifiziert die komplette Attestation-Chain (Reproduzierbarkeit + Linkage).

    Exit 0 = jede Attestation nachrechenbar und die Chain lückenlos; Exit 1 sonst.
    """
    from app.truth.ledger import DEFAULT_TRUTH_LEDGER_PATH, verify_ledger

    report = verify_ledger(Path(ledger_path) if ledger_path else DEFAULT_TRUTH_LEDGER_PATH)
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        raise typer.Exit(1)


@trading_app.command("truth-attest-file")
def truth_attest_file(
    artifact: str = typer.Argument(..., help="JSON-Artefakt (Objekt), das attestiert wird"),
    kind: str = typer.Option(
        "artifact", "--kind", help="Artefakt-Art (z. B. canonical_edge_report)"
    ),
    ledger_path: str | None = typer.Option(
        None, "--ledger-path", help="Truth-Attestation-Ledger JSONL (default: kanonischer Pfad)"
    ),
    audit_mirror: bool = typer.Option(
        True, "--audit-mirror/--no-audit-mirror", help="Attestation in den KAI-Audit spiegeln"
    ),
) -> None:
    """Attestiert ein beliebiges JSON-Artefakt (z. B. einen gespeicherten Report)."""
    from app.truth.ledger import DEFAULT_TRUTH_LEDGER_PATH, append_attestation

    payload = json.loads(Path(artifact).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        typer.secho("Artefakt muss ein JSON-Objekt sein.", err=True)
        raise typer.Exit(2)
    record = append_attestation(
        kind,
        None,
        payload,
        path=Path(ledger_path) if ledger_path else DEFAULT_TRUTH_LEDGER_PATH,
        mirror_audit=audit_mirror,
    )
    print(
        json.dumps(
            {k: record[k] for k in ("seq", "kind", "subject_id", "payload_hash", "record_hash")},
            indent=2,
        )
    )


@trading_app.command("compliance-export")
def compliance_export(
    ledger_path: str | None = typer.Option(
        None, "--ledger-path", help="Provenienz-Ledger JSONL (default: kanonischer Pfad)"
    ),
    out: str | None = typer.Option(None, "--out", help="Export als Datei statt stdout"),
) -> None:
    """Konsolidierter SoF/TFR/Steuer-Export aus dem Provenienz-Ledger (read-only)."""
    from app.compliance.provenance import (
        DEFAULT_PROVENANCE_LEDGER_PATH,
        compute_compliance_export,
        read_provenance_records,
    )

    records = read_provenance_records(
        Path(ledger_path) if ledger_path else DEFAULT_PROVENANCE_LEDGER_PATH
    )
    export = compute_compliance_export(records)
    rendered = json.dumps(export, indent=2, ensure_ascii=False)
    if out:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"export geschrieben: {out_path}")
    else:
        print(rendered)


@trading_app.command("provenance-record")
def provenance_record(
    kind: str = typer.Option(..., "--kind", help="ownership_proof | whitelist | transfer"),
    wallet: str = typer.Option(..., "--wallet", help="Wallet-Adresse"),
    method: str | None = typer.Option(None, "--method", help="z. B. satoshi_test / signature"),
    tx_hash: str | None = typer.Option(None, "--tx-hash"),
    counterparty: str | None = typer.Option(None, "--counterparty", help="z. B. die Exchange"),
    amount: float | None = typer.Option(None, "--amount"),
    currency: str | None = typer.Option(None, "--currency"),
    note: str | None = typer.Option(None, "--note"),
    ledger_path: str | None = typer.Option(
        None, "--ledger-path", help="Provenienz-Ledger JSONL (default: kanonischer Pfad)"
    ),
) -> None:
    """Hängt einen validierten Nachweis-Eintrag an das Provenienz-Ledger an."""
    from app.compliance.provenance import (
        DEFAULT_PROVENANCE_LEDGER_PATH,
        ProvenanceRecord,
        append_provenance_record,
    )

    record = ProvenanceRecord(
        kind=kind,  # type: ignore[arg-type]  # Literal-Validierung übernimmt pydantic
        timestamp=datetime.now(UTC).isoformat(),
        wallet_address=wallet,
        method=method,
        tx_hash=tx_hash,
        counterparty=counterparty,
        amount=amount,
        currency=currency,
        note=note,
    )
    append_provenance_record(
        record, path=Path(ledger_path) if ledger_path else DEFAULT_PROVENANCE_LEDGER_PATH
    )
    print(record.model_dump_json(indent=2))


@trading_app.command("capital-snapshot")
def capital_snapshot(
    balances: str = typer.Option(
        ..., "--balances", help='Bucket-Salden als JSON, z. B. {"operating": 6000}'
    ),
    gain: float | None = typer.Option(
        None, "--gain", help="Realisierter Gewinn (USD) für eine Reserve-Empfehlung"
    ),
    split: float = typer.Option(0.5, "--split", help="Gewinn-Anteil Richtung Reserve [0..1]"),
    reserve_target: float = typer.Option(
        0.0, "--reserve-target", help="Reserve-Zielhöhe (USD) — Überlauf geht in Langfrist"
    ),
    current_reserve: float | None = typer.Option(
        None, "--current-reserve", help="Aktuelle Reserve (default: reserve-Bucket der Salden)"
    ),
) -> None:
    """Shadow-Snapshot der Kapital-Buckets (+ optionale Gewinn-Split-Empfehlung).

    Reine Rechenausgabe — es wird nichts bewegt (`executes=false`).
    """
    from app.capital.reserve_policy import compute_reserve_recommendation
    from app.capital.segmentation import compute_segmentation_snapshot

    snapshot = compute_segmentation_snapshot(json.loads(balances))
    result = {"snapshot": snapshot, "recommendation": None}
    if gain is not None:
        result["recommendation"] = compute_reserve_recommendation(
            gain,
            current_reserve_usd=(
                current_reserve if current_reserve is not None else snapshot["by_bucket"]["reserve"]
            ),
            profit_split_pct=split,
            reserve_target_usd=reserve_target,
        )
    print(json.dumps(result, indent=2))
