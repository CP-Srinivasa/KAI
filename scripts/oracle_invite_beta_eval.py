#!/usr/bin/env python3
"""Evaluator der Prä-Reg ``oracle_invite_beta_v1`` (D-299 A1) — lesend, deterministisch.

Zählt distinkte eingeladene DRITTE (``invite_party == third_party``), deren Einladung
mindestens eine bezahlte Oracle-Abfrage im versiegelten Fenster ausgelöst hat:

    settled Zahlung (ln_earnings_ledger, Memo ``kai-oracle:``, ``settled_at`` im Fenster)
      --payment_hash-->  ``l402_challenge_minted`` im Demand-Ledger  -->  invite_id / party

Eigene Einladungen (``operator``) und Zahlungen ohne Einladung zählen nicht; sie werden
ausgewiesen statt verschwiegen. Vor Fensterende lautet das Ergebnis ``PENDING`` — nie
ein Verdikt.

Die Regel steht in ``config/oracle_invite_beta_v1.json`` und bindet diesen Evaluator
per sha256. Weicht der Hash ab, bricht der Lauf ab (Exit 2): eine nach der Versiegelung
geänderte Zählweise wäre Backfitting.

    python scripts/oracle_invite_beta_eval.py [--artifacts artifacts] [--now ISO-ZEIT]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RULE_PATH = Path("config/oracle_invite_beta_v1.json")
_THIRD = "third_party"
_OPERATOR = "operator"


def _parse_ts(raw: object) -> datetime | None:
    """ISO-Zeit oder lnd-``settle_date`` (Unix-Sekunden); ``None`` bei Unlesbarem."""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(int(text), UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # eine kaputte Zeile darf die Auswertung nicht kippen
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _invite_by_payment(demand_rows: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """payment_hash -> (invite_id, party) aus den Challenge-Events (erste Nennung gewinnt)."""
    index: dict[str, tuple[str, str]] = {}
    for row in demand_rows:
        if row.get("event") != "l402_challenge_minted":
            continue
        payment_hash = str(row.get("payment_hash") or "")
        if payment_hash and payment_hash not in index:
            index[payment_hash] = (
                str(row.get("invite_id") or ""),
                str(row.get("invite_party") or ""),
            )
    return index


def evaluate(
    *,
    rule: dict[str, Any],
    earnings_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Reine Auswertung nach der versiegelten Regel."""
    start = _parse_ts(rule["window_start_utc"])
    end = _parse_ts(rule["window_end_utc"])
    if start is None or end is None or end <= start:
        raise ValueError("Regel ohne gültiges Fenster")
    success = rule["success"]
    prefix = str(success["memo_prefix"])
    index = _invite_by_payment(demand_rows)

    paid_by_invite: dict[str, int] = {}
    excluded = {
        "outside_window": 0,
        "no_settle_time": 0,
        "payments_without_invite": 0,
        "operator_payments": 0,
        "unknown_party": 0,
    }
    for row in earnings_rows:
        if not str(row.get("memo") or "").startswith(prefix):
            continue  # keine Oracle-Zahlung
        settled = _parse_ts(row.get("settled_at"))
        if settled is None:
            excluded["no_settle_time"] += 1
            continue
        if not start <= settled < end:
            excluded["outside_window"] += 1
            continue
        invite_id, party = index.get(str(row.get("payment_hash") or ""), ("", ""))
        if not invite_id:
            excluded["payments_without_invite"] += 1
        elif party == _OPERATOR:
            excluded["operator_payments"] += 1
        elif party != _THIRD:
            excluded["unknown_party"] += 1
        else:
            paid_by_invite[invite_id] = paid_by_invite.get(invite_id, 0) + 1

    per_invite = int(success["min_settled_payments_per_invite"])
    qualified = sorted(i for i, n in paid_by_invite.items() if n >= per_invite)
    target = int(success["min_distinct_third_party_invites"])
    if now < end:
        verdict = "PENDING"
    elif len(qualified) >= target:
        verdict = "MET"
    else:
        verdict = "NOT_MET"
    return {
        "schema": "oracle_invite_beta_eval/v1",
        "name": rule.get("name"),
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "window_closed": now >= end,
        "evaluated_at_utc": now.isoformat(),
        "distinct_third_party_invites": len(qualified),
        "target": target,
        "qualified_invite_ids": qualified,
        "excluded": excluded,
        "verdict_proposal": verdict,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default="artifacts")
    parser.add_argument("--rule", default=str(RULE_PATH))
    parser.add_argument("--now", default="")
    args = parser.parse_args(argv)

    rule_path = Path(args.rule)
    rule = json.loads(rule_path.read_text(encoding="utf-8"))
    own = _sha256(Path(__file__))
    if rule["evaluator"]["sha256"] != own:
        print(f"ABBRUCH: Evaluator-Hash {own} weicht von der Regel ab.", file=sys.stderr)
        return 2
    now = _parse_ts(args.now) if args.now else datetime.now(UTC)
    if now is None:
        print("ABBRUCH: --now unlesbar.", file=sys.stderr)
        return 2
    art = Path(args.artifacts)
    result = evaluate(
        rule=rule,
        earnings_rows=_read_jsonl(art / "ln_earnings_ledger.jsonl"),
        demand_rows=_read_jsonl(art / "ln_demand_ledger.jsonl"),
        now=now,
    )
    result["rule_sha256"] = _sha256(rule_path)
    result["evaluator_sha256"] = own
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
