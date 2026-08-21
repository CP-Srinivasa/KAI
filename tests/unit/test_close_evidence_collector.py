"""Der Collector sammelt Tatsachen — und fällt kein Urteil.

Geprüft wird der Vertrag, nicht die Bequemlichkeit: vollständige Identität,
explizite Venue, enges Primärfenster, saubere Zeitsemantik, kanonische Bytes,
Hash **vor** dem Schreiben, atomare Veröffentlichung und fail-closed bei
abweichender Evidenz für dieselbe Close-Identität.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.execution.close_evidence import EVIDENCE_SCHEMA_VERSION, VenueCandle, canonical_bytes
from app.execution.close_evidence_collector import (
    PRIMARY_INTERVAL,
    CollectionStatus,
    build_close_evidence,
    collector_code_sha,
    publish_evidence,
)

MINUTE = 60_000
CLOSE_TS = "2026-08-21T09:00:30+00:00"
CLOSE_MS = int(datetime.fromisoformat(CLOSE_TS).timestamp() * 1000)
NOW = datetime(2026, 8, 21, 9, 5, 0, tzinfo=UTC)


def _close(**kw) -> dict[str, object]:
    row = {
        "event_type": "position_closed",
        "fill_id": "fill_abc",
        "order_id": "ord_abc",
        "symbol": "ETH/USDT",
        "timestamp_utc": CLOSE_TS,
        "exit_price": 100.0,
    }
    row.update(kw)
    return row


def _fetcher(candles=None, *, record=None):
    data = (
        candles
        if candles is not None
        else [
            VenueCandle(CLOSE_MS - MINUTE, 99.0, 100.5, 98.5, 100.0),
            VenueCandle(CLOSE_MS - (CLOSE_MS % MINUTE), 100.0, 101.0, 99.5, 100.8),
        ]
    )

    def fetch(*, symbol, venue, interval, start_ms, end_ms):
        if record is not None:
            record.append(
                {
                    "symbol": symbol,
                    "venue": venue,
                    "interval": interval,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                }
            )
        return list(data)

    return fetch


# --- Der Collector urteilt nicht -------------------------------------------------


def test_collector_faellt_kein_urteil() -> None:
    """Kein verified/plausibel/quarantine — nur, wie die SAMMLUNG ausging."""
    values = {s.value for s in CollectionStatus}
    for verboten in ("verified", "plausible", "quarantine", "corrupt", "phantom"):
        assert not any(verboten in v for v in values), f"{verboten} gehoert nicht in den Collector"


def test_collector_modul_enthaelt_keine_urteilslogik() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "app" / "execution" / "close_evidence_collector.py"
    ).read_text(encoding="utf-8")
    # Der Collector darf den Verifier nicht importieren — sonst wandert das Urteil
    # zurueck in die Schicht mit Netzzugriff.
    assert "from app.execution.close_verifier import" not in source
    assert "verify_close" not in source


# --- Identität, Venue, Zeit ------------------------------------------------------


@pytest.mark.parametrize("feld", ["fill_id", "order_id", "symbol"])
def test_unvollstaendige_identitaet_wird_abgewiesen(feld: str) -> None:
    r = build_close_evidence(_close(**{feld: ""}), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert r.status is CollectionStatus.CLOSE_IDENTITY_INCOMPLETE
    assert feld in r.detail


def test_venue_muss_explizit_sein() -> None:
    """Nachtraeglich aus price_source zu raten waere erfundene Provenienz."""
    r = build_close_evidence(_close(), venue="", fetch=_fetcher(), now_utc=NOW)
    assert r.status is CollectionStatus.CLOSE_IDENTITY_INCOMPLETE
    assert "venue" in r.detail


@pytest.mark.parametrize("bad", ["", "kaputt", "2026-08-21T09:00:30"])
def test_unbrauchbare_close_zeit_wird_abgewiesen(bad: str) -> None:
    """Auch der naive Zeitstempel — als UTC anzunehmen waere Raten."""
    r = build_close_evidence(
        _close(timestamp_utc=bad), venue="bybit", fetch=_fetcher(), now_utc=NOW
    )
    assert r.status is CollectionStatus.INVALID_CLOSE_TIMESTAMP


def test_alle_identitaeten_landen_im_artefakt() -> None:
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    ev = r.evidence
    assert ev is not None
    assert ev.close_fill_id == "fill_abc"
    assert ev.close_order_id == "ord_abc"
    assert ev.symbol == "ETH/USDT"
    assert ev.close_timestamp_utc == CLOSE_TS
    assert ev.venue == "bybit"


# --- Fenster ---------------------------------------------------------------------


def test_primaerfenster_ist_minutengenau_und_eng() -> None:
    """Ein weites Fenster macht jeden Preis plausibel — genau das nicht."""
    calls: list[dict] = []
    build_close_evidence(_close(), venue="bybit", fetch=_fetcher(record=calls), now_utc=NOW)
    assert len(calls) == 1
    call = calls[0]
    assert call["interval"] == PRIMARY_INTERVAL == "1m"
    spannweite = call["end_ms"] - call["start_ms"]
    assert spannweite <= 4 * MINUTE, "Fenster zu breit"
    assert call["start_ms"] < CLOSE_MS < call["end_ms"]


def test_leeres_fenster_ist_fehlende_evidenz() -> None:
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher([]), now_utc=NOW)
    assert r.status is CollectionStatus.WINDOW_UNAVAILABLE
    assert r.evidence is None


def test_kerzen_aus_der_zukunft_werden_abgewiesen() -> None:
    """Was nach dem Sammeln liegt, ist keine Beobachtung."""
    future = VenueCandle(int((NOW + timedelta(minutes=5)).timestamp() * 1000), 1, 2, 0.5, 1.5)
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher([future]), now_utc=NOW)
    assert r.status is CollectionStatus.CANDLES_IN_FUTURE


def test_kerzen_werden_nach_zeit_sortiert() -> None:
    unsorted_ = [
        VenueCandle(CLOSE_MS, 100.0, 101.0, 99.5, 100.8),
        VenueCandle(CLOSE_MS - MINUTE, 99.0, 100.5, 98.5, 100.0),
    ]
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(unsorted_), now_utc=NOW)
    assert r.evidence is not None
    zeiten = [c.open_time_ms for c in r.evidence.candles]
    assert zeiten == sorted(zeiten)


def test_collected_at_ist_utc_und_traegt_die_sammlerversion() -> None:
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    ev = r.evidence
    assert ev is not None
    assert ev.collected_at_utc.endswith("+00:00")
    assert ev.collector_code_sha == collector_code_sha()
    assert ev.schema_version == EVIDENCE_SCHEMA_VERSION


# --- Veröffentlichung: atomar, verankert, idempotent -----------------------------


def test_artefakt_wird_unter_seinem_hash_abgelegt(tmp_path: Path) -> None:
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    r = publish_evidence(built.evidence, tmp_path)

    assert r.status is CollectionStatus.COLLECTED
    path = Path(r.path)
    assert path.name == f"{built.payload_sha256}.json"
    assert path.parent.name == "fill_abc"
    # Die geschriebenen Bytes sind exakt die kanonischen — der Hash passt zur Datei.
    assert path.read_bytes() == canonical_bytes(built.evidence.as_payload())


def test_manifest_verankert_den_hash(tmp_path: Path) -> None:
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    publish_evidence(built.evidence, tmp_path)

    manifest = json.loads((tmp_path / "fill_abc" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["payload_sha256"] == built.payload_sha256
    assert manifest["fill_id"] == "fill_abc"
    assert manifest["order_id"] == "ord_abc"
    assert manifest["venue"] == "bybit"
    assert manifest["schema_version"] == EVIDENCE_SCHEMA_VERSION


def test_zweiter_lauf_mit_gleicher_evidenz_ist_ein_noop(tmp_path: Path) -> None:
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    first = publish_evidence(built.evidence, tmp_path)
    second = publish_evidence(built.evidence, tmp_path)

    assert first.status is CollectionStatus.COLLECTED
    assert second.status is CollectionStatus.IDEMPOTENT_NOOP
    assert second.payload_sha256 == first.payload_sha256


def test_abweichende_evidenz_ist_ein_konflikt_und_ueberschreibt_nichts(tmp_path: Path) -> None:
    """Der Fall, wenn ein Provider historische Kerzen spaeter anders liefert."""
    first_built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert first_built.evidence is not None
    publish_evidence(first_built.evidence, tmp_path)

    geaendert = [VenueCandle(CLOSE_MS - MINUTE, 99.0, 200.0, 98.5, 100.0)]
    second_built = build_close_evidence(
        _close(), venue="bybit", fetch=_fetcher(geaendert), now_utc=NOW
    )
    assert second_built.evidence is not None
    conflict = publish_evidence(second_built.evidence, tmp_path)

    assert conflict.status is CollectionStatus.EVIDENCE_CONFLICT
    assert first_built.payload_sha256 in conflict.detail
    # Das erste Artefakt bleibt unangetastet, das zweite wurde NICHT geschrieben.
    manifest = json.loads((tmp_path / "fill_abc" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["payload_sha256"] == first_built.payload_sha256
    assert not (tmp_path / "fill_abc" / f"{second_built.payload_sha256}.json").exists()


def test_unlesbares_manifest_ist_ein_konflikt_kein_ueberschreiben(tmp_path: Path) -> None:
    folder = tmp_path / "fill_abc"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text("{kaputt", encoding="utf-8")

    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    r = publish_evidence(built.evidence, tmp_path)
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT


def test_keine_temporaerdateien_bleiben_zurueck(tmp_path: Path) -> None:
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    publish_evidence(built.evidence, tmp_path)
    assert not list((tmp_path / "fill_abc").glob("*.tmp"))


def test_der_verifier_akzeptiert_das_veroeffentlichte_artefakt(tmp_path: Path) -> None:
    """Ende-zu-Ende: der verankerte Hash ist der, den der Verifier verlangt."""
    from app.execution.close_verifier import ReasonCode, verify_close

    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    published = publish_evidence(built.evidence, tmp_path)

    result = verify_close(
        _close(),
        built.evidence,
        expected_evidence_sha256=published.payload_sha256,
    )
    assert ReasonCode.MISSING_EVIDENCE_HASH not in result.reasons
    assert ReasonCode.EVIDENCE_HASH_MISMATCH not in result.reasons
    assert ReasonCode.EVIDENCE_ORDER_MISMATCH not in result.reasons
    assert ReasonCode.EVIDENCE_CLOSE_TIME_MISMATCH not in result.reasons
