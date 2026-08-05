#!/usr/bin/env python3
"""C1-Zahlungs-Zweig-Evaluator — die fehlende Messvorschrift zu ``9cab81fae4823482``.

Die Prä-Registrierung ``oracle_demand_probe_fee_truth_v1`` trägt ``gate=null``
(free-text-era): ``prereg-check`` kann sie strukturell nicht beurteilen und
verweist selbst auf manuelle Beurteilung. Genau diese Lücke schließt dieses
Skript — es macht die am 2026-08-02 **vor** Fensterende versiegelte
Auswertungsvorschrift (``artifacts/research/c1_evaluation_rule_20260802.json``)
ausführbar, statt sie ein zweites Mal in Prosa zu wiederholen.

Bindung an die Regel statt Duplikat:
  * Fenster, Quellen und gezählte Scopes werden aus der Regeldatei GELESEN.
  * Die Regeldatei wird gehasht; der Hash steht im Ergebnis. Wer die Regel
    nachträglich ändert, ändert den Hash und macht das sichtbar.
  * Die beiden Schwellen (>=5 Zahlungen, >=3 distinkte Payer) stammen wörtlich
    aus dem ``success_criteria`` der Prä-Registrierung; das Skript verifiziert
    ihr Vorkommen im ``criterion``-Text der Regel und bricht sonst ab.

Payer-Identität: der Earnings-Ledger führt keinen Fingerprint. Die Zuordnung
läuft über ``payment_hash`` gegen ``l402_challenge_minted`` im Demand-Ledger —
derselbe Join, über den eine Zahlung überhaupt einem Requester zurechenbar ist.

Read-only. Schreibt nichts, bewegt kein Kapital, gatet nichts. Ausgabe ist das
Evaluator-JSON für ``trading verdict-report``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Wörtlich aus success_criteria der Prä-Reg 9cab81fae4823482:
# ">=5 settled L402-Payments von >=3 distinkten Payern".
MIN_PAYMENTS = 5
MIN_DISTINCT_PAYERS = 3

# Memo-Präfix, über den eine Zahlung als Oracle-L402-Zahlung erkennbar ist.
ORACLE_MEMO_PREFIX = "kai-oracle:"

# Fensterstart-Spanne, für die die Regel Ergebnis-Invarianz behauptet.
INVARIANCE_STARTS = ("2026-07-02T00:00:00+00:00", "2026-08-03T00:00:00+00:00")


def _parse_ts(raw: Any) -> datetime | None:
    """ISO-8601 oder Unix-Sekunden (auch als String) -> aware UTC datetime."""
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return datetime.fromtimestamp(float(raw), tz=UTC)
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=UTC)
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
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # eine kaputte Zeile darf die Auswertung nicht kippen
    return rows


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payer_index(demand_rows: list[dict[str, Any]]) -> dict[str, str]:
    """payment_hash -> requester_fp aus den Challenge-Events (erste Nennung gewinnt)."""
    index: dict[str, str] = {}
    for row in demand_rows:
        if row.get("event") != "l402_challenge_minted":
            continue
        payment_hash = str(row.get("payment_hash") or "")
        fingerprint = str(row.get("requester_fp") or "")
        if payment_hash and fingerprint and payment_hash not in index:
            index[payment_hash] = fingerprint
    return index


def _settle_time(row: dict[str, Any]) -> tuple[datetime | None, str]:
    """Maßgeblich ist ``settled_at``; ``ts`` ist nur der Buchungszeitpunkt."""
    settled = _parse_ts(row.get("settled_at"))
    if settled is not None:
        return settled, "settled_at"
    return _parse_ts(row.get("ts")), "ts_fallback"


def evaluate(
    *,
    rule: dict[str, Any],
    earnings_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    """Zahlungs-Zweig gegen ein Fenster rechnen. Reine Funktion."""
    scopes = set(rule["sealed_rule"]["payment_branch"]["scopes_counted"])
    payers = _payer_index(demand_rows)

    qualifying: list[dict[str, Any]] = []
    considered: list[dict[str, Any]] = []
    for row in earnings_rows:
        memo = str(row.get("memo") or "")
        settled, time_source = _settle_time(row)
        scope = memo[len(ORACLE_MEMO_PREFIX) :] if memo.startswith(ORACLE_MEMO_PREFIX) else None
        record = {
            "payment_hash": row.get("payment_hash"),
            "amount_sat": row.get("amount_sat"),
            "source": row.get("source"),
            "memo": memo,
            "scope": scope,
            "settled_at_utc": settled.isoformat() if settled else None,
            "settle_time_source": time_source,
            "payer_fp": payers.get(str(row.get("payment_hash") or "")),
        }
        reasons: list[str] = []
        if scope is None:
            reasons.append("memo trägt keinen kai-oracle:-Präfix")
        elif scope not in scopes:
            reasons.append(f"scope {scope!r} nicht in gezählten Scopes")
        if settled is None:
            reasons.append("kein auswertbarer Settle-Zeitstempel")
        elif not (window_start <= settled <= window_end):
            reasons.append("settled ausserhalb des Fensters")
        record["excluded_because"] = reasons
        considered.append(record)
        if not reasons:
            qualifying.append(record)

    distinct = sorted({r["payer_fp"] for r in qualifying if r["payer_fp"]})
    n_payments = len(qualifying)
    n_payers = len(distinct)
    checks = [
        {
            "name": "settled_payments",
            "required": f">={MIN_PAYMENTS}",
            "actual": n_payments,
            "ok": n_payments >= MIN_PAYMENTS,
        },
        {
            "name": "distinct_payer_fingerprints",
            "required": f">={MIN_DISTINCT_PAYERS}",
            "actual": n_payers,
            "ok": n_payers >= MIN_DISTINCT_PAYERS,
        },
    ]
    passed = all(c["ok"] for c in checks)
    return {
        "window": {"start_utc": window_start.isoformat(), "end_utc": window_end.isoformat()},
        "passed": passed,
        "verdict": "PASS" if passed else "FAIL",
        "checks": checks,
        "settled_payments_in_window": n_payments,
        "distinct_payer_fps_in_window": n_payers,
        "distinct_payer_fps": distinct,
        "qualifying_payments": qualifying,
        "earnings_rows_considered": considered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rule",
        default="artifacts/research/c1_evaluation_rule_20260802.json",
        help="Versiegelte Auswertungsvorschrift",
    )
    parser.add_argument("--artifacts-dir", default="artifacts", help="Artifacts-Wurzel")
    parser.add_argument("--json", dest="as_json", action="store_true", help="JSON auf stdout")
    args = parser.parse_args()

    rule_path = Path(args.rule)
    rule = json.loads(rule_path.read_text(encoding="utf-8"))
    branch = rule["sealed_rule"]["payment_branch"]

    # Divergenzschutz: die Schwellen dieses Skripts müssen im Regeltext stehen.
    criterion = str(branch["criterion"])
    for threshold in (MIN_PAYMENTS, MIN_DISTINCT_PAYERS):
        if f">={threshold}" not in criterion.replace(" ", ""):
            raise SystemExit(
                f"Regeltext nennt die Schwelle >={threshold} nicht — "
                f"Skript und versiegelte Regel sind divergiert: {criterion!r}"
            )

    root = Path(args.artifacts_dir)
    sources = {}
    earnings_rows: list[dict[str, Any]] = []
    demand_rows: list[dict[str, Any]] = []
    for rel in branch["source_of_truth"]:
        path = Path(rel) if Path(rel).is_absolute() else root / Path(rel).name
        rows = _read_jsonl(path)
        sources[str(path)] = {
            "rows": len(rows),
            "sha256": _sha256_file(path) if path.is_file() else None,
        }
        if "earnings" in path.name:
            earnings_rows = rows
        elif "demand" in path.name:
            demand_rows = rows

    sealed_start = _parse_ts(branch["window"]["start_utc"])
    sealed_end = _parse_ts(branch["window"]["end_utc"])
    assert sealed_start is not None and sealed_end is not None, "Fenster der Regel unlesbar"

    primary = evaluate(
        rule=rule,
        earnings_rows=earnings_rows,
        demand_rows=demand_rows,
        window_start=sealed_start,
        window_end=sealed_end,
    )

    # Invarianz-Nachweis: die Regel behauptet Ergebnis-Gleichheit über die
    # Fensterstart-Spanne. Behauptung wird gerechnet, nicht geglaubt.
    sensitivity = []
    for start_iso in INVARIANCE_STARTS:
        start = _parse_ts(start_iso)
        assert start is not None
        alt = evaluate(
            rule=rule,
            earnings_rows=earnings_rows,
            demand_rows=demand_rows,
            window_start=start,
            window_end=sealed_end,
        )
        sensitivity.append(
            {
                "window_start_utc": start_iso,
                "verdict": alt["verdict"],
                "settled_payments": alt["settled_payments_in_window"],
                "distinct_payer_fps": alt["distinct_payer_fps_in_window"],
            }
        )
    invariant = all(s["verdict"] == primary["verdict"] for s in sensitivity)

    result = {
        "evaluator": "c1_payment_branch_eval",
        "evaluator_version": 1,
        "prereg_id": rule["prereg_id"],
        "hypothesis": rule["hypothesis"],
        "rule_file": str(rule_path),
        "rule_sha256": _sha256_file(rule_path),
        "rule_sealed_at_utc": rule["sealed_at_utc"],
        "thresholds": {
            "min_settled_payments": MIN_PAYMENTS,
            "min_distinct_payer_fps": MIN_DISTINCT_PAYERS,
        },
        "scopes_counted": sorted(branch["scopes_counted"]),
        "sources": sources,
        "or_branch": {
            "status": rule["sealed_rule"]["or_branch"]["status"],
            "note": "nicht ausgewertet — trägt weder PASS noch FAIL bei (versiegelt 2026-08-02)",
        },
        "payment_branch": primary,
        "window_start_sensitivity": sensitivity,
        "verdict_invariant_over_window_start": invariant,
        "verdict": primary["verdict"],
        "passed": primary["passed"],
    }

    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        pb = result["payment_branch"]
        print(f"{result['verdict']}  ({result['hypothesis']} / {result['prereg_id']})")
        for check in pb["checks"]:
            mark = "OK " if check["ok"] else "NOK"
            print(
                f"  {mark} {check['name']}: required={check['required']} actual={check['actual']}"
            )
        print(f"  Fenster: {pb['window']['start_utc']} .. {pb['window']['end_utc']}")
        print(f"  Verdikt invariant über Fensterstart-Spanne: {invariant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
