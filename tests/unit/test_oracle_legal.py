"""Oracle-Rechtsseiten (Operator-Entscheid 2026-09-30, D-291 E1/E6/E9).

Zwei öffentliche Seiten ohne Kundenkonto, vor der Zahlung verlinkt, online erst nach
Freigabe durch den Anwalt. Diese Tests halten fest:
- Solange offene Punkte in den Vorlagen stehen, ist nichts öffentlich, auch mit Schalter.
- Die Vorschau zeigt ENTWURF und markiert jeden offenen Punkt.
- Meldung und Widerruf erzeugen einen belegten Vorgang mit Vorgangsnummer; der Betreiber
  wird ohne personenbezogene Angaben benachrichtigt; Geheimnisse werden abgewiesen.
- Die 402-Antwort verlinkt Bedingungen und Hilfe erst nach der Freigabe.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import oracle_legal as legal
from app.api.routers import oracle_legal as router_mod
from app.api.routers import truth_oracle
from app.lightning.receive_gate import ValueLayerResult


def _settings(*, published: bool) -> SimpleNamespace:
    return SimpleNamespace(
        lightning=SimpleNamespace(
            oracle_legal_published=published,
            l402_default_price_sat=10,
            l402_secret="legal-test-secret",
        )
    )


@pytest.fixture
def app_client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(legal, "CASES_PATH", tmp_path / "cases.jsonl")
    monkeypatch.setattr(router_mod, "_limiter", legal.RateLimiter(per_key=3, total=10))
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def released(monkeypatch) -> None:
    """Freigegebener Zustand: Schalter an, Vorlagen ohne offene Punkte."""
    original = legal._template

    def _clean(page: str) -> str:
        return legal._OPEN.sub("freigegebener Text", original(page))

    monkeypatch.setattr(legal, "_template", _clean)
    monkeypatch.setattr(router_mod, "get_settings", lambda: _settings(published=True))


# ---------------------------------------------------------------- Freigabe


def test_beta_v05_has_no_open_items_so_the_switch_decides() -> None:
    # Operator 01.10.: Beta-Texte v0.5 freigegeben ("erstmal so verwenden").
    assert legal.open_items() == []
    assert legal.PAGES == ("bedingungen", "hilfe", "datenschutz")
    assert legal.is_published(True) is True
    assert legal.is_published(False) is False


def test_a_draft_awaiting_the_lawyer_blocks_publication_like_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for page in legal.PAGES:
        (tmp_path / f"{page}.html").write_text("<p>fertig</p>", encoding="utf-8")
    monkeypatch.setattr(legal, "_DIR", tmp_path)
    assert legal.is_published(True) is True
    (tmp_path / "hilfe.html").write_text("<p>[[PRUEFEN: Entwurf X]] Text</p>", encoding="utf-8")
    assert legal.open_items() == ["ANWALT PRÜFT: Entwurf X"]
    assert legal.is_published(True) is False
    html_out = legal.render("hilfe", price_sat=10, access_min=60, invoice_min=5, preview=True)
    assert '<mark class="offen">ANWALT PRÜFT: Entwurf X</mark> Text' in html_out


@pytest.mark.parametrize("path", ["/oracle/bedingungen", "/oracle/hilfe", "/oracle/datenschutz"])
def test_public_pages_stay_hidden_while_the_switch_is_off(app_client, monkeypatch, path) -> None:
    monkeypatch.setattr(router_mod, "get_settings", lambda: _settings(published=False))
    assert app_client.get(path).status_code == 404


def test_forms_stay_closed_before_release(app_client, monkeypatch) -> None:
    monkeypatch.setattr(router_mod, "get_settings", lambda: _settings(published=False))
    r = app_client.post("/oracle/hilfe/meldung", data={"problem": "anderes"})
    assert r.status_code == 404
    assert not legal.CASES_PATH.exists()


@pytest.mark.parametrize("seite", ["bedingungen", "hilfe", "datenschutz"])
def test_preview_marks_the_draft_and_fills_every_placeholder(
    app_client, monkeypatch, seite
) -> None:
    monkeypatch.setattr(router_mod, "get_settings", lambda: _settings(published=False))
    page = app_client.get(f"/dashboard/api/oracle/rechtsseiten/{seite}").text
    assert "ENTWURF – nicht freigegeben" in page
    assert 'content="noindex, nofollow"' in page
    assert "{{" not in page and "[[" not in page
    assert "Version 0.5-Beta" in page


def test_status_endpoint_lists_open_items(app_client, monkeypatch) -> None:
    monkeypatch.setattr(router_mod, "get_settings", lambda: _settings(published=True))
    body = app_client.get("/dashboard/api/oracle/rechtsseiten").json()
    assert body["switch_on"] is True and body["published"] is True
    assert body["open_items"] == legal.open_items() == []


def test_pages_state_the_real_access_rule() -> None:
    page = legal.render("bedingungen", price_sat=10, access_min=60, invoice_min=5, preview=False)
    assert "mindestens 60 Minuten nach erfolgreicher Zahlung" in page
    assert "10 sat einschließlich etwaiger gesetzlich geschuldeter Umsatzsteuer" in page
    assert "für 5 Minuten gültige Lightning-Zahlungsanforderung" in page
    assert "X-KAI-Invite" in page
    # Die Zahlen stammen aus dem Oracle selbst, nicht aus dem Text.
    assert truth_oracle._ACCESS_WINDOW_S // 60 == 60
    assert truth_oracle._INVOICE_EXPIRY_MINUTES == 5


# ---------------------------------------------------------------- nach Freigabe


def test_released_pages_are_public_and_indexable(app_client, released) -> None:
    for path in ("/oracle/bedingungen", "/oracle/hilfe", "/oracle/datenschutz"):
        r = app_client.get(path)
        assert r.status_code == 200
        assert "ENTWURF" not in r.text and 'content="index, follow"' in r.text


_REPORT = {
    "problem": "nicht_erhalten",
    "referenz": "ab" * 32,
    "bereich": "verdicts",
    "zeitpunkt": "30.09.2026 14:05 MESZ",
    "beschreibung": "Bezahlt, danach nur 503 bis zum Ablauf.",
    "email": "kunde@example.org",
}


def test_report_creates_a_case_and_notifies_without_personal_data(app_client, released) -> None:
    notify = AsyncMock(return_value=True)
    with patch("app.alerts.notify.send_operator_notification", notify):
        r = app_client.post("/oracle/hilfe/meldung", data=_REPORT)
    assert r.status_code == 201
    [case] = [json.loads(x) for x in legal.CASES_PATH.read_text(encoding="utf-8").splitlines()]
    assert case["case_id"].startswith("KAI-O-") and case["case_id"] in r.text
    assert case["kind"] == "meldung" and case["terms_version"] == legal.VERSION
    assert case["email"] == "kunde@example.org"
    sent = notify.await_args.args[0]
    assert case["case_id"] in sent
    assert "kunde@example.org" not in sent and "503" not in sent


def test_there_is_no_online_withdrawal_only_email_or_post(app_client, released) -> None:
    """Operator 01.10.: ohne SMTP keine Online-Widerrufsfunktion.

    Wer einen Online-Widerruf anbietet, muss den Eingang unverzueglich auf einem
    dauerhaften Datentraeger bestaetigen (§ 356 Abs. 1 BGB) -- ohne E-Mail-Versand
    nicht erfuellbar. Widerrufe gehen deshalb per E-Mail oder Post; die Route ist weg,
    damit kein unverlinkter Endpunkt Widerrufe ohne Bestaetigung annimmt.
    """
    r = app_client.post("/oracle/hilfe/widerruf", data={"referenz": "cd" * 32, "email": "a@b.de"})
    assert r.status_code in (404, 405)
    page = app_client.get("/oracle/hilfe").text
    assert 'action="/oracle/hilfe/widerruf"' not in page
    assert "info@formsys.io" in page and "Widerrufsfunktion im Web gibt es deshalb nicht" in page
    terms = app_client.get("/oracle/bedingungen").text
    assert "keine Bestelloberfläche mit elektronischer Widerrufsfunktion" in terms
    assert "Freiwillige Mustervorlage" in terms


def test_privacy_page_promises_only_what_the_retention_job_does() -> None:
    """Die Datenschutzseite darf keine Löschung zusagen, die der Code nicht leistet."""
    from app.oracle_legal import invites, retention

    page = legal.render("datenschutz", price_sat=10, access_min=60, invoice_min=5, preview=False)
    # IP: knapp vor 6 Tagen entfernt; mit täglichem Lauf (24 h) und Laufzeit bleibt das
    # unter den zugesagten 7 Tagen.
    assert retention.IP_PROMISE == timedelta(days=7)
    assert retention.IP_RETENTION + timedelta(days=1, hours=1) < retention.IP_PROMISE
    assert "Schutzkennwerte: spätestens sieben Tage nach Erhebung" in page
    assert "Löschung nach 14 Tagen" in page  # logrotate rotate 14 (deploy/logrotate/kai)
    assert retention.CASE_RETENTION_AFTER_CLOSE.days == 90 and "90 Tage nach Abschluss" in page
    assert invites.RETAIN_AFTER_END.days == 30 and "30 Tage nach Ende der Einladung" in page
    assert "bis zu zwölf Monate" in page  # Offsite-Vault-Generationen
    assert "frei erfundene" not in page and "Betriebsnachweis" not in page


@pytest.mark.parametrize(
    "change",
    [
        {"beschreibung": "L402 AgEDbG5k...:abcd"},
        {"beschreibung": "Mein Token: " + "A" * 120},
        {"email": "keine-adresse"},
        {"problem": "gibt-es-nicht"},
        {"referenz": "<script>"},
        {"website": "spam"},
    ],
)
def test_bad_or_secret_input_is_rejected(app_client, released, change) -> None:
    with patch("app.alerts.notify.send_operator_notification", AsyncMock(return_value=True)):
        r = app_client.post("/oracle/hilfe/meldung", data={**_REPORT, **change})
    assert r.status_code == 422
    assert not legal.CASES_PATH.exists()


def test_rate_limit(app_client, released) -> None:
    with patch("app.alerts.notify.send_operator_notification", AsyncMock(return_value=True)):
        codes = [
            app_client.post("/oracle/hilfe/meldung", data=_REPORT).status_code for _ in range(4)
        ]
    assert codes == [201, 201, 201, 429]


def test_receipt_escapes_user_input(tmp_path: Path) -> None:
    case = legal.record_case(
        "meldung",
        {**_REPORT, "beschreibung": "<img src=x onerror=alert(1)>"},
        now=datetime(2026, 9, 30, tzinfo=UTC),
        path=tmp_path / "cases.jsonl",
    )
    assert "<img" not in legal.receipt_html(case)
    assert "&lt;img" in legal.receipt_html(case)


# ---------------------------------------------------------------- 402-Antwort

_PREIMAGE = "44" * 32
_PH_HEX = hashlib.sha256(bytes.fromhex(_PREIMAGE)).hexdigest()


def _oracle_settings(*, published: bool) -> SimpleNamespace:
    return SimpleNamespace(
        integrity=SimpleNamespace(enabled=True, stamper="opentimestamps"),
        lightning=SimpleNamespace(
            l402_enabled=True,
            l402_secret="oracle-legal-secret",
            l402_default_price_sat=10,
            l402_mint_per_min=100,
            l402_mint_budget_per_min=100,
            l402_invite_required=False,  # Einladung: test_oracle_invite.py
            oracle_legal_published=published,
        ),
    )


def _challenge(monkeypatch, tmp_path: Path, *, published: bool, clean: bool):
    truth_oracle.reset_mint_limiter()
    ledger = tmp_path / "demand.jsonl"
    monkeypatch.setattr("app.lightning.demand_ledger._DEMAND_PATH", ledger)
    if clean:
        original = legal._template
        monkeypatch.setattr(legal, "_template", lambda p: legal._OPEN.sub("x", original(p)))
    inv = ValueLayerResult(
        "create_invoice",
        "executed",
        "",
        response={
            "r_hash": base64.b64encode(bytes.fromhex(_PH_HEX)).decode(),
            "payment_request": "lnbc100n1...",
        },
    )
    chain = SimpleNamespace(
        state="ok",
        reachable=True,
        chain="main",
        blocks=1,
        headers=1,
        best_block_hash="ab" * 32,
        synced=True,
    )
    app = FastAPI()
    app.include_router(truth_oracle.router)
    with (
        patch.object(
            truth_oracle, "get_settings", return_value=_oracle_settings(published=published)
        ),
        patch.object(truth_oracle, "create_invoice", AsyncMock(return_value=inv)),
        patch("app.chain.cache.get_cached_chain_status", AsyncMock(return_value=(chain, 1.0))),
    ):
        r = TestClient(app, raise_server_exceptions=False).get("/oracle/onchain-facts")
    events = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    return r, events


def test_challenge_unchanged_before_release(monkeypatch, tmp_path: Path) -> None:
    r, events = _challenge(monkeypatch, tmp_path, published=False, clean=False)
    assert r.status_code == 402
    assert r.json()["detail"] == "payment required"
    assert "Link" not in r.headers
    assert "terms_version" not in events[0]


def test_challenge_links_terms_and_help_after_release(monkeypatch, tmp_path: Path) -> None:
    r, events = _challenge(monkeypatch, tmp_path, published=True, clean=True)
    assert r.status_code == 402
    assert 'rel="terms-of-service"' in r.headers["Link"] and 'rel="help"' in r.headers["Link"]
    assert 'rel="privacy-policy"' in r.headers["Link"]
    body = r.json()["detail"]
    assert body["terms"] == "/oracle/bedingungen" and body["help"] == "/oracle/hilfe"
    assert body["privacy"] == "/oracle/datenschutz"
    assert body["price_sat"] == 10 and body["access_expires"] == r.headers["X-L402-Access-Expires"]
    assert "mindestens 60 Minuten" in body["notice"]
    assert events[0]["terms_version"] == legal.VERSION
