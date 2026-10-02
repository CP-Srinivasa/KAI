"""Messbarkeit der Oracle-Einladungsbeta (D-299 A1, Prae-Reg oracle_invite_beta_v1).

Vorher war das Erfolgskriterium nicht zaehlbar: keine Zahlung war mit einer Einladung
verknuepft, die einzige Kaeuferspur (IP-Kennwert) wird nach sechs Tagen geleert, und
Eigen-Traffic war nicht markiert. Hier wird festgehalten:

* jede Einladung traegt ``party`` (``third_party`` oder ``operator``),
* die Rechnungsausstellung schreibt ``invite_id``/``invite_party`` ins Demand-Ledger,
* der Loeschjob leert nur den IP-Kennwert, nicht die Einladungskennung,
* die Datenschutzseite sagt genau das,
* der Evaluator zaehlt distinkte eingeladene Dritte mit bezahlter Abfrage im Fenster,
* Regeldatei und Evaluator sind per sha256 aneinander gebunden.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routers import truth_oracle
from app.lightning import demand_ledger
from app.oracle_legal import invites, retention

_NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
_REPO = Path(__file__).resolve().parents[2]
_RULE = _REPO / "config" / "oracle_invite_beta_v1.json"
_EVALUATOR = _REPO / "scripts" / "oracle_invite_beta_eval.py"


def _request(code: str | None = None) -> Request:
    headers = [(b"x-kai-invite", code.encode())] if code else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/oracle/fee-series",
            "headers": headers,
            "query_string": b"",
        }
    )


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "invites.jsonl"
    monkeypatch.setattr(invites, "INVITES_PATH", path)
    return path


# ── Einladungen: Partei und Zuordnung ───────────────────────────────────────


def test_einladung_traegt_partei_dritte_ist_standard(store: Path) -> None:
    _code, fremd = invites.create("Partner A", now=_NOW)
    _code2, eigen = invites.create("Ich selbst", party="operator", now=_NOW)

    assert fremd["party"] == "third_party"
    assert eigen["party"] == "operator"
    with pytest.raises(ValueError):
        invites.create("X", party="freund", now=_NOW)


def test_match_liefert_die_einladung_statt_nur_ja_nein(store: Path) -> None:
    code, entry = invites.create("Partner B", days=10, now=_NOW)
    invites.create("Partner C", now=_NOW)

    hit = invites.match(code, now=_NOW + timedelta(days=1))
    assert hit is not None and hit["id"] == entry["id"] and hit["party"] == "third_party"
    assert invites.match(code, now=_NOW + timedelta(days=11)) is None  # abgelaufen
    assert invites.match("falsch", now=_NOW) is None
    invites.revoke(entry["id"], now=_NOW + timedelta(hours=1))
    assert invites.match(code, now=_NOW + timedelta(hours=2)) is None
    assert invites.is_valid(code, now=_NOW + timedelta(hours=2)) is False


# ── Rechnungsausstellung schreibt die Einladungskennung ins Ledger ──────────


def test_require_paid_reicht_einladung_an_die_rechnung_durch(store: Path, monkeypatch) -> None:  # noqa: ANN001
    code, entry = invites.create("Partner D")
    settings = SimpleNamespace(
        lightning=SimpleNamespace(l402_secret="s", l402_invite_required=True)
    )
    monkeypatch.setattr(truth_oracle, "_require_oracle_enabled", lambda: settings)
    monkeypatch.setattr(truth_oracle, "_valid_paid_token", lambda request, scope: None)
    seen: dict = {}

    async def _gate(request, scope):  # noqa: ANN001, ANN202
        return None

    async def _issue(scope, **kwargs):  # noqa: ANN001, ANN003, ANN202
        seen.update(kwargs)
        raise HTTPException(status_code=402)

    monkeypatch.setattr(truth_oracle, "_gate_mint", _gate)
    monkeypatch.setattr(truth_oracle, "_issue_challenge", _issue)
    with pytest.raises(HTTPException):
        anyio.run(lambda: truth_oracle._require_paid(_request(code), "fee-series"))

    assert seen["invite_id"] == entry["id"]
    assert seen["invite_party"] == "third_party"


def test_challenge_zeile_traegt_einladung(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    ledger = tmp_path / "ln_demand_ledger.jsonl"
    monkeypatch.setattr(demand_ledger, "_DEMAND_PATH", ledger)
    settings = SimpleNamespace(
        lightning=SimpleNamespace(
            l402_default_price_sat=10, l402_secret="s", oracle_legal_published=False
        )
    )
    monkeypatch.setattr(truth_oracle, "get_settings", lambda: settings)

    async def _invoice(**kwargs):  # noqa: ANN003, ANN202
        return SimpleNamespace(
            state="executed",
            response={
                "r_hash": base64.b64encode(b"\x01" * 32).decode(),
                "payment_request": "lnbc1",
            },
            detail="",
        )

    monkeypatch.setattr(truth_oracle, "create_invoice", _invoice)
    with pytest.raises(HTTPException) as exc:
        anyio.run(
            lambda: truth_oracle._issue_challenge(
                "fee-series", requester_fp="fp1", invite_id="inv_abcd1234", invite_party="operator"
            )
        )
    assert exc.value.status_code == 402
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert row["event"] == demand_ledger.CHALLENGE_MINTED
    assert row["invite_id"] == "inv_abcd1234" and row["invite_party"] == "operator"
    assert row["payment_hash"] == (b"\x01" * 32).hex()


def test_ohne_einladung_bleibt_die_zeile_wie_bisher(tmp_path: Path) -> None:
    ledger = tmp_path / "d.jsonl"
    demand_ledger.append_demand_event(demand_ledger.CHALLENGE_MINTED, scope="x", path=ledger)
    row = json.loads(ledger.read_text(encoding="utf-8"))
    assert "invite_id" not in row and "invite_party" not in row


# ── Loeschjob und Datenschutzseite ──────────────────────────────────────────


def test_loeschjob_leert_kennwert_aber_nicht_die_einladung(tmp_path: Path) -> None:
    ledger = tmp_path / "d.jsonl"
    old = (_NOW - timedelta(days=10)).isoformat()
    ledger.write_text(
        json.dumps(
            {
                "ts": old,
                "event": demand_ledger.CHALLENGE_MINTED,
                "requester_fp": "fp_alt",
                "payment_hash": "ab" * 32,
                "invite_id": "inv_abcd1234",
                "invite_party": "third_party",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert retention.strip_demand_fingerprints(ledger, _NOW) == 1
    row = json.loads(ledger.read_text(encoding="utf-8"))
    assert row["requester_fp"] == ""
    assert row["invite_id"] == "inv_abcd1234" and row["invite_party"] == "third_party"


def test_datenschutzseite_nennt_die_einladungskennung() -> None:
    from app import oracle_legal as legal

    page = legal.render("datenschutz", price_sat=10, access_min=60, invoice_min=5, preview=False)
    assert "zufällige Einladungskennung" in page
    assert "ohne Namen oder Kontaktdaten" in page


# ── Operator-Skript ─────────────────────────────────────────────────────────


def test_skript_markiert_eigene_einladungen(store: Path, capsys) -> None:  # noqa: ANN001
    from scripts.oracle_invite import main

    assert main(["neu", "Ich (Test)", "--eigen"]) == 0
    assert main(["neu", "Partner E", "--tage", "7"]) == 0
    parties = {e["label"]: e["party"] for e in invites.listing()}
    assert parties == {"Ich (Test)": "operator", "Partner E": "third_party"}
    capsys.readouterr()
    assert main(["liste"]) == 0
    assert "eigen" in capsys.readouterr().out


# ── Evaluator und Regel ─────────────────────────────────────────────────────


def _rule() -> dict:
    return json.loads(_RULE.read_text(encoding="utf-8"))


def _challenge(ph: str, invite_id: str | None, party: str = "third_party") -> dict:
    row = {"event": "l402_challenge_minted", "payment_hash": ph, "scope": "fee-series"}
    if invite_id:
        row.update(invite_id=invite_id, invite_party=party)
    return row


def _earning(ph: str, settled: datetime, memo: str = "kai-oracle:fee-series") -> dict:
    return {
        "payment_hash": ph,
        "amount_sat": 10,
        "memo": memo,
        "settled_at": str(int(settled.timestamp())),
    }


def test_regel_ist_an_den_evaluator_gebunden() -> None:
    rule = _rule()
    assert rule["evaluator"]["path"] == "scripts/oracle_invite_beta_eval.py"
    assert rule["evaluator"]["sha256"] == hashlib.sha256(_EVALUATOR.read_bytes()).hexdigest()
    assert rule["success"]["min_distinct_third_party_invites"] == 3
    start = datetime.fromisoformat(rule["window_start_utc"])
    end = datetime.fromisoformat(rule["window_end_utc"])
    assert end - start == timedelta(days=60)


def test_evaluator_zaehlt_nur_distinkte_dritte_im_fenster() -> None:
    from scripts.oracle_invite_beta_eval import evaluate

    rule = _rule()
    start = datetime.fromisoformat(rule["window_start_utc"])
    inside = start + timedelta(days=3)
    demand = [
        _challenge("a1", "inv_aaaa0001"),
        _challenge("a2", "inv_aaaa0001"),  # zweite Zahlung derselben Einladung
        _challenge("b1", "inv_bbbb0002"),
        _challenge("o1", "inv_eeee0009", party="operator"),  # Eigen-Traffic
        _challenge("n1", None),  # ohne Einladung
        _challenge("c1", "inv_cccc0003"),
        _challenge("x1", "inv_dddd0004"),
    ]
    earnings = [
        _earning("a1", inside),
        _earning("a2", inside + timedelta(days=1)),
        _earning("b1", inside),
        _earning("o1", inside),
        _earning("n1", inside),
        _earning("c1", start - timedelta(days=1)),  # vor dem Fenster
        _earning("x1", inside, memo="kai-pay:etwas"),  # keine Oracle-Zahlung
    ]
    result = evaluate(rule=rule, earnings_rows=earnings, demand_rows=demand, now=inside)

    assert result["distinct_third_party_invites"] == 2
    assert result["excluded"]["operator_payments"] == 1
    assert result["excluded"]["payments_without_invite"] == 1
    assert result["verdict_proposal"] == "PENDING"  # Fenster noch offen


def test_evaluator_urteilt_erst_nach_fensterende() -> None:
    from scripts.oracle_invite_beta_eval import evaluate

    rule = _rule()
    start = datetime.fromisoformat(rule["window_start_utc"])
    end = datetime.fromisoformat(rule["window_end_utc"])
    inside = start + timedelta(days=5)
    demand = [_challenge(f"p{i}", f"inv_0000000{i}") for i in range(3)]
    earnings = [_earning(f"p{i}", inside) for i in range(3)]

    met = evaluate(rule=rule, earnings_rows=earnings, demand_rows=demand, now=end)
    assert met["verdict_proposal"] == "MET" and met["distinct_third_party_invites"] == 3
    short = evaluate(rule=rule, earnings_rows=earnings[:2], demand_rows=demand, now=end)
    assert short["verdict_proposal"] == "NOT_MET"
