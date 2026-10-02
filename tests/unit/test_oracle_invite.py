"""Begleitete Oracle-Beta: neue Rechnungen nur mit Einladung (Operator 01.10.2026, v0.5).

Hält fest: ohne gültigen Code keine Rechnung (403, kein Mint), mit Code wie bisher 402;
Header und Query-Parameter gehen; abgelaufene und gesperrte Codes nicht; der Klartext-
Code steht nirgends; Einladungen verschwinden 30 Tage nach ihrem Ende.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routers import truth_oracle
from app.oracle_legal import invites

_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _request(*, header: str | None = None, query: str = "") -> Request:
    headers = [(b"x-kai-invite", header.encode())] if header else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/oracle/fee-series",
            "headers": headers,
            "query_string": query.encode(),
        }
    )


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "invites.jsonl"
    monkeypatch.setattr(invites, "INVITES_PATH", path)
    return path


def test_code_is_valid_until_expiry_and_never_stored_in_plain(store: Path) -> None:
    code, entry = invites.create("Max (Partner)", days=30, now=_NOW)
    assert invites.is_valid(code, now=_NOW + timedelta(days=29))
    assert not invites.is_valid(code, now=_NOW + timedelta(days=30, seconds=1))
    assert not invites.is_valid(code + "x", now=_NOW)
    assert not invites.is_valid(None, now=_NOW) and not invites.is_valid("", now=_NOW)
    assert code not in store.read_text(encoding="utf-8")
    assert entry["label"] == "Max (Partner)"


def test_a_revoked_code_stops_working(store: Path) -> None:
    code, entry = invites.create("Anna", now=_NOW)
    invites.revoke(entry["id"], now=_NOW + timedelta(hours=1))
    assert not invites.is_valid(code, now=_NOW + timedelta(hours=2))
    assert invites.listing()[0]["revoked_at"]
    with pytest.raises(KeyError):
        invites.revoke("inv_unbekannt")


def test_invites_are_deleted_30_days_after_they_end(store: Path) -> None:
    _old, gone = invites.create("Alt", days=1, now=_NOW - timedelta(days=40))
    keep_code, kept = invites.create("Neu", days=30, now=_NOW)
    assert invites.prune(now=_NOW) == 1
    ids = {json.loads(line)["id"] for line in store.read_text(encoding="utf-8").splitlines()}
    assert ids == {kept["id"]} and invites.is_valid(keep_code, now=_NOW)


def test_create_rejects_empty_label_and_silly_durations(store: Path) -> None:
    with pytest.raises(ValueError):
        invites.create("   ")
    with pytest.raises(ValueError):
        invites.create("x", days=0)


def test_without_invite_no_invoice_is_issued(store: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        truth_oracle._require_invite(_request(), True)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "invitation_required"
    assert "info@formsys.io" in exc.value.detail["message"]


def test_a_valid_invite_in_header_or_query_passes(store: Path) -> None:
    code, _entry = invites.create("Partner")
    truth_oracle._require_invite(_request(header=code), True)
    truth_oracle._require_invite(_request(query=f"invite={code}"), True)
    with pytest.raises(HTTPException):
        truth_oracle._require_invite(_request(header="falsch"), True)


def test_the_gate_can_be_switched_off(store: Path) -> None:
    truth_oracle._require_invite(_request(), False)


def test_unpaid_route_without_invite_never_mints(store: Path, monkeypatch) -> None:  # noqa: ANN001
    """End-to-end über _require_paid: 403 kommt VOR Limiter und Rechnung."""
    from types import SimpleNamespace

    import anyio

    settings = SimpleNamespace(
        lightning=SimpleNamespace(l402_secret="s", l402_invite_required=True)
    )
    monkeypatch.setattr(truth_oracle, "_require_oracle_enabled", lambda: settings)
    monkeypatch.setattr(truth_oracle, "_valid_paid_token", lambda request, scope: None)
    calls: list[str] = []

    async def _gate(request, scope):  # noqa: ANN001, ANN202
        calls.append("gate")

    async def _issue(*a, **k):  # noqa: ANN002, ANN003, ANN202
        calls.append("mint")

    monkeypatch.setattr(truth_oracle, "_gate_mint", _gate)
    monkeypatch.setattr(truth_oracle, "_issue_challenge", _issue)
    with pytest.raises(HTTPException) as exc:
        anyio.run(lambda: truth_oracle._require_paid(_request(), "fee-series"))
    assert exc.value.status_code == 403 and calls == []


def test_operator_cli_creates_lists_and_revokes(store: Path, capsys) -> None:  # noqa: ANN001
    from scripts.oracle_invite import main

    assert main(["neu", "Lisa", "--tage", "7"]) == 0
    out = capsys.readouterr().out
    code = out.split("Code (nur jetzt sichtbar): ")[1].split()[0]
    assert invites.is_valid(code)
    inv_id = invites.listing()[0]["id"]
    assert main(["liste"]) == 0 and "aktiv" in capsys.readouterr().out
    assert main(["sperren", inv_id]) == 0
    assert not invites.is_valid(code)
    assert main(["quatsch"]) == 2


def test_watcher_flags_an_unreadable_invite_store(tmp_path: Path) -> None:
    from app.alerts.health_check_payments import check_oracle_cases

    assert check_oracle_cases(tmp_path, now=_NOW) == []  # keine Datei: Normalfall
    invites.create("Ok", path=tmp_path / "oracle" / "invites.jsonl")
    assert check_oracle_cases(tmp_path, now=_NOW) == []
    with (tmp_path / "oracle" / "invites.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("kaputt\n")
    [issue] = check_oracle_cases(tmp_path, now=_NOW)
    assert issue.component == "oracle_invites" and "1 unlesbare" in issue.message


# ---------------------------------------------------------------------------
# Bereiche (Operator-Entscheid E2, 02.10.2026): eine Einladung gilt fuer Teilnehmer +
# erlaubte Bereiche + Ablauf. Ein Bereich ausserhalb der Einladung bekommt KEINE Rechnung
# (403 vor Limiter und Mint). Neue Einladungen bekommen standardmaessig nur die
# betriebsfertigen Bereiche; "timestamp" erst ausdruecklich (7-Tage-Erstattungszusage).
# ---------------------------------------------------------------------------


def test_new_invites_default_to_the_ready_scopes(store: Path) -> None:
    code, entry = invites.create("Partner", now=_NOW)
    assert entry["scopes"] == sorted(invites.READY_SCOPES)
    assert "timestamp" not in entry["scopes"]
    found = invites.match(code, now=_NOW)
    assert found is not None and found["id"] == entry["id"] and found["party"] == "third_party"
    assert found["scopes"] == sorted(invites.READY_SCOPES)


def test_a_scope_outside_the_invite_gets_no_invoice(store: Path) -> None:
    code, _entry = invites.create("Partner", scopes=["onchain-facts"])
    truth_oracle._require_invite(_request(header=code), True, scope="onchain-facts")
    with pytest.raises(HTTPException) as exc:
        truth_oracle._require_invite(_request(header=code), True, scope="verdicts")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "invitation_scope"
    assert exc.value.detail["allowed_scopes"] == ["onchain-facts"]


def test_an_invite_from_before_scopes_allows_every_scope(store: Path) -> None:
    """Rueckwaertskompatibel: Eintraege ohne ``scopes`` (vor dem 02.10.) gelten fuer alle."""
    code, entry = invites.create("Alt", now=_NOW)
    rows = [json.loads(line) for line in store.read_text(encoding="utf-8").splitlines()]
    rows[0].pop("scopes")
    store.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    found = invites.match(code, now=_NOW)
    assert found is not None and found["scopes"] is None
    truth_oracle._require_invite(_request(header=code), True, scope="timestamp")


def test_unknown_or_empty_scopes_are_refused_on_create(store: Path) -> None:
    with pytest.raises(ValueError):
        invites.create("x", scopes=["alles"])
    with pytest.raises(ValueError):
        invites.create("x", scopes=[])


def test_require_paid_checks_the_public_scope_before_minting(store: Path, monkeypatch) -> None:  # noqa: ANN001
    from types import SimpleNamespace

    import anyio

    settings = SimpleNamespace(
        lightning=SimpleNamespace(l402_secret="s", l402_invite_required=True)
    )
    monkeypatch.setattr(truth_oracle, "_require_oracle_enabled", lambda: settings)
    monkeypatch.setattr(truth_oracle, "_valid_paid_token", lambda request, scope: None)
    calls: list[str] = []

    async def _gate(request, scope):  # noqa: ANN001, ANN202
        calls.append("gate")

    async def _issue(*a, **k):  # noqa: ANN002, ANN003, ANN202
        calls.append("mint")
        raise HTTPException(status_code=402, detail="payment required")

    monkeypatch.setattr(truth_oracle, "_gate_mint", _gate)
    monkeypatch.setattr(truth_oracle, "_issue_challenge", _issue)
    code, _entry = invites.create("Partner", scopes=["onchain-facts", "timestamp"])

    with pytest.raises(HTTPException) as exc:
        anyio.run(lambda: truth_oracle._require_paid(_request(header=code), "fee-series"))
    assert exc.value.status_code == 403 and calls == [], "falscher Bereich: kein Limiter, kein Mint"

    with pytest.raises(HTTPException) as exc:
        anyio.run(
            lambda: truth_oracle._require_paid(
                _request(header=code), "timestamp:" + "ab" * 32, telemetry_scope="timestamp"
            )
        )
    assert exc.value.status_code == 402 and calls == ["gate", "mint"]


def test_operator_cli_takes_scopes_in_any_order(store: Path, capsys) -> None:  # noqa: ANN001
    from scripts.oracle_invite import main

    assert (
        main(
            ["neu", "Ich (Test)", "--bereiche", "onchain-facts,timestamp", "--eigen", "--tage", "5"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "onchain-facts, timestamp" in out
    [entry] = invites.listing()
    assert entry["scopes"] == ["onchain-facts", "timestamp"] and entry["party"] == "operator"
    assert main(["neu", "Y", "--bereiche", "unbekannt"]) == 2
    assert main(["liste"]) == 0
    assert "onchain-facts,timestamp" in capsys.readouterr().out
