#!/usr/bin/env python3
"""Messvorschrift zur Prä-Reg ``0879a65c5fd01f65`` (ln_reconciliation_shadow_integrity_v1).

Die Prä-Registrierung wurde am 2026-08-08T10:50:09Z **vor** dem Scharfschalten
von ``kai-ln-reconcile.timer`` versiegelt, trägt aber ``gate=null``: der
strukturelle ``prereg-check`` kann sie nicht beurteilen und verweist auf
manuelle Bewertung. Genau diese Lücke schließt dieses Skript — sonst wäre das
Verdikt zum Fälligkeitstag nicht reproduzierbar und ein FAIL nicht von einem
kaputten Auswertungsskript zu unterscheiden (Lehre aus C1, #630).

Bindung an die versiegelte Konstruktion statt Duplikat:
  * Fenster (``created_at_utc`` + ``horizon``) und Stichprobenziel
    (``sample_size_target``) werden aus dem Prä-Reg-Satz GELESEN.
  * Der ``success_criteria``-Text wird gehasht; der Hash steht im Ergebnis.
  * Die Schlüsselklauseln werden wörtlich im Text verifiziert. Weicht er ab,
    bricht das Skript mit ``CriteriaDivergenceError`` ab, statt zu raten.

Die beiden versiegelten Achsen:
  * **Sicherheit/Tip** — jeder Lauf im Fenster muss Truth-tip-Containment
    bestehen, und kein nicht berechtigter Intent (unsupported, unmatched,
    ambiguous, amount-mismatched, nonterminal) darf terminalisiert worden sein.
  * **Transitions-Wirksamkeit** — jeder natürlich beobachtete, eindeutig
    zugeordnete terminale Treffer muss genau einmal angehängt worden sein.
    Ohne Vorfälle bleibt diese Achse ``INSUFFICIENT_N``; dann darf laut
    versiegeltem Text nur die Sicherheitsachse bestehen.

Read-only. Schreibt nichts, bewegt kein Kapital, gatet nichts. Kein
Readiness-, Kapital-, Alpha- oder Umsatz-Anspruch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PREREG_ID = "0879a65c5fd01f65"
DEFAULT_PREREG_LEDGER = Path("artifacts/research/prereg_ledger.jsonl")
DEFAULT_REPORT_PATH = Path("artifacts/lightning/ln_reconciliation.jsonl")

# Wörtliche Klauseln aus dem versiegelten ``success_criteria``. Fehlt eine,
# ist der Text nicht mehr der, gegen den hier gemessen wird.
REQUIRED_CLAUSES = (
    "96 enabled shadow runs within 7d",
    "all runs pass Truth-tip containment",
    "zero unsupported, unmatched, ambiguous, amount-mismatched or nonterminal intents",
    "appended exactly once by the next completed run",
    "transition-effectiveness remains INSUFFICIENT_N",
    "only the safety/tip axis may pass",
)

# ``reason``-Werte aus app/lightning/reconciliation.py, bei denen eine
# Terminalisierung laut versiegeltem Text NICHT stattfinden darf.
INELIGIBLE_REASONS = frozenset(
    {
        "unsupported_action",
        "payment_not_found",
        "ambiguous_payment_hash",
        "amount_mismatch",
        "node_payment_nonterminal",
        "invalid_intent_payment_hash",
    }
)
# Berechtigte Vorfaelle: ein eindeutig zugeordneter terminaler Treffer, der die
# Anhang-Stufe erreicht hat. ``journal_append_failed`` gehoert dazu — der
# Treffer war berechtigt, nur der Anhang misslang; ihn hier zu uebergehen
# wuerde genau den Fehlerfall unsichtbar machen, den die Achse messen soll.
ELIGIBLE_REASONS = frozenset({"node_terminal_match", "journal_append_failed"})
TERMINALISED_PREFIX = "journalled_"


class CriteriaDivergenceError(RuntimeError):
    """Der versiegelte Kriterientext passt nicht mehr zu dieser Messvorschrift."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_horizon(raw: Any) -> timedelta:
    match = re.fullmatch(r"\s*(\d+)\s*([dh])\s*", str(raw or ""))
    if not match:
        raise CriteriaDivergenceError(f"horizon nicht interpretierbar: {raw!r}")
    value = int(match.group(1))
    return timedelta(days=value) if match.group(2) == "d" else timedelta(hours=value)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def load_prereg(ledger_path: Path, prereg_id: str = PREREG_ID) -> dict[str, Any]:
    """Den versiegelten Prä-Reg-Satz aus dem Ledger holen (letzter Treffer gewinnt)."""
    found: dict[str, Any] | None = None
    for row in read_jsonl(ledger_path):
        if str(row.get("prereg_id") or "") == prereg_id:
            found = row
    if found is None:
        raise LookupError(f"prereg_id {prereg_id} nicht in {ledger_path}")
    return found


def _verify_criteria(criteria: str) -> None:
    missing = [clause for clause in REQUIRED_CLAUSES if clause not in criteria]
    if missing:
        raise CriteriaDivergenceError(
            "versiegelter Kriterientext enthaelt diese Klauseln nicht mehr: " + "; ".join(missing)
        )


def _select_runs(
    runs: list[dict[str, Any]], *, window_start: datetime, window_end: datetime, limit: int
) -> list[dict[str, Any]]:
    """Läufe im Fenster, zeitlich sortiert, auf die ersten ``limit`` beschnitten."""
    dated: list[tuple[datetime, dict[str, Any]]] = []
    for run in runs:
        moment = _parse_ts(run.get("ts"))
        if moment is None or moment < window_start or moment > window_end:
            continue
        dated.append((moment, run))
    dated.sort(key=lambda item: item[0])
    return [run for _, run in dated[:limit]]


def evaluate(*, prereg: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    criteria = str(prereg.get("success_criteria") or "")
    _verify_criteria(criteria)

    window_start = _parse_ts(prereg.get("created_at_utc"))
    if window_start is None:
        raise CriteriaDivergenceError("created_at_utc fehlt im Prae-Reg-Satz")
    window_end = window_start + _parse_horizon(prereg.get("horizon"))
    target = int(prereg.get("sample_size_target") or 0)
    if target <= 0:
        raise CriteriaDivergenceError("sample_size_target fehlt oder ist nicht positiv")

    counted = _select_runs(runs, window_start=window_start, window_end=window_end, limit=target)

    tip_failures = 0
    illegal: list[dict[str, Any]] = []
    unappended: list[dict[str, Any]] = []
    appended_by_intent: dict[str, int] = {}
    eligible_incidents = 0

    for run in counted:
        cross = run.get("tip_cross_check")
        contained = bool(cross.get("contained")) if isinstance(cross, dict) else False
        if not contained:
            tip_failures += 1
        intents = run.get("intents")
        if not isinstance(intents, list):
            continue
        for item in intents:
            if not isinstance(item, dict):
                continue
            reason = str(item.get("reason") or "")
            result = str(item.get("result") or "")
            intent_id = str(item.get("intent_id") or "")
            terminalised = result.startswith(TERMINALISED_PREFIX)
            if reason in INELIGIBLE_REASONS and terminalised:
                illegal.append({"ts": run.get("ts"), "intent_id": intent_id, "reason": reason})
            if reason not in ELIGIBLE_REASONS:
                continue
            eligible_incidents += 1
            if terminalised:
                appended_by_intent[intent_id] = appended_by_intent.get(intent_id, 0) + 1
            else:
                unappended.append({"ts": run.get("ts"), "intent_id": intent_id, "result": result})

    duplicates = [key for key, count in appended_by_intent.items() if count > 1]

    safety_passed = tip_failures == 0 and not illegal
    safety_axis = {
        "passed": safety_passed,
        "tip_containment_failures": tip_failures,
        "illegal_terminalisations": len(illegal),
        "illegal_examples": illegal[:5],
    }

    if eligible_incidents == 0:
        transition_status = "INSUFFICIENT_N"
    elif duplicates or unappended:
        transition_status = "FAIL"
    else:
        transition_status = "PASS"
    transition_axis = {
        "status": transition_status,
        "eligible_incidents": eligible_incidents,
        "duplicate_terminalisations": len(duplicates),
        "unappended_matches": len(unappended),
        "unappended_examples": unappended[:5],
    }

    mature = len(counted) >= target
    if not mature:
        verdict = "IMMATURE"
    elif not safety_passed or transition_status == "FAIL":
        verdict = "FAIL"
    else:
        verdict = "PASS"

    scope_note = (
        "Nur die Sicherheits-/Tip-Achse ist bestanden; Transitions-Wirksamkeit bleibt "
        "INSUFFICIENT_N (keine berechtigten Vorfaelle im Fenster) — so im versiegelten "
        "Text vorgesehen."
        if transition_status == "INSUFFICIENT_N"
        else ""
    )

    return {
        "evaluator": "ln_reconciliation_eval",
        "evaluator_version": 1,
        "prereg_id": str(prereg.get("prereg_id") or ""),
        "hypothesis": str(prereg.get("name") or ""),
        "success_criteria_sha256": _sha256_text(criteria),
        "window": {
            "start_utc": window_start.isoformat(),
            "end_utc": window_end.isoformat(),
            "sample_size_target": target,
        },
        "runs_counted": len(counted),
        "mature": mature,
        "safety_axis": safety_axis,
        "transition_axis": transition_axis,
        "scope_note": scope_note,
        "claim_note": (
            "Betriebliche Integritaets-Hypothese. Kein Readiness-, Kapital-, Alpha- oder "
            "Umsatz-Anspruch."
        ),
        "verdict": verdict,
        "passed": verdict == "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg-ledger", type=Path, default=DEFAULT_PREREG_LEDGER)
    parser.add_argument("--reports", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--prereg-id", default=PREREG_ID)
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args()

    prereg = load_prereg(args.prereg_ledger, args.prereg_id)
    result = evaluate(prereg=prereg, runs=read_jsonl(args.reports))

    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print(f"{result['verdict']}  ({result['hypothesis']} / {result['prereg_id']})")
        print(
            f"  Laeufe im Fenster: {result['runs_counted']}/{result['window']['sample_size_target']}"
        )
        safety = result["safety_axis"]
        print(
            f"  Sicherheit/Tip: passed={safety['passed']} "
            f"tip_failures={safety['tip_containment_failures']} "
            f"illegale_terminalisierungen={safety['illegal_terminalisations']}"
        )
        transition = result["transition_axis"]
        print(
            f"  Transition: {transition['status']} "
            f"vorfaelle={transition['eligible_incidents']} "
            f"doppelt={transition['duplicate_terminalisations']} "
            f"ohne_anhang={transition['unappended_matches']}"
        )
        if result["scope_note"]:
            print(f"  {result['scope_note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
