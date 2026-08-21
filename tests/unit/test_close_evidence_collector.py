"""Der Collector sammelt Tatsachen — und fällt kein Urteil.

Geprüft wird der Vertrag, nicht die Bequemlichkeit: vollständige Identität,
explizite Venue, festes Primärfenster, saubere Zeitsemantik, abgeschlossene
Kerzen, kanonische Bytes, Hash **vor** dem Schreiben, das Manifest als
Commit-Marker, ein Schreiber je Close und echte Retry-Idempotenz.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.execution.close_evidence import EVIDENCE_SCHEMA_VERSION, VenueCandle, canonical_bytes
from app.execution.close_evidence_collector import (
    PRIMARY_INTERVAL,
    PRIMARY_WINDOW_RADIUS_MINUTES,
    CollectionStatus,
    build_close_evidence,
    collect_and_publish,
    collector_code_sha,
    publish_evidence,
)

MINUTE = 60_000
CLOSE_TS = "2026-08-21T09:00:30+00:00"
CLOSE_MS = int(datetime.fromisoformat(CLOSE_TS).timestamp() * 1000)
M0 = CLOSE_MS - 30_000  # 09:00:00 — die Kerze, die den Close enthaelt
M_PREV = M0 - MINUTE  # 08:59:00
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


def _default_candles() -> list[VenueCandle]:
    return [
        VenueCandle(M_PREV, 99.0, 100.5, 98.5, 100.0),
        VenueCandle(M0, 100.0, 101.0, 99.5, 100.8),
    ]


def _fetcher(candles=None, *, record=None, raises: Exception | None = None):
    data = _default_candles() if candles is None else candles

    def fetch(*, symbol, venue, interval, start_ms, end_ms):
        if raises is not None:
            raise raises
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


def _the_folder(tmp_path: Path) -> Path:
    """Das eine Unterverzeichnis unterhalb von base_dir."""
    subs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(subs) == 1, subs
    return subs[0]


# --- Der Collector urteilt nicht -------------------------------------------------


def test_collector_faellt_kein_urteil() -> None:
    values = {s.value for s in CollectionStatus}
    for verboten in ("verified", "plausible", "quarantine", "corrupt", "phantom"):
        assert not any(verboten in v for v in values), f"{verboten} gehoert nicht in den Collector"


def test_collector_modul_enthaelt_keine_urteilslogik() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "app" / "execution" / "close_evidence_collector.py"
    ).read_text(encoding="utf-8")
    assert "from app.execution.close_verifier import" not in source
    assert "verify_close" not in source


# --- Identität, Venue, Zeit ------------------------------------------------------


@pytest.mark.parametrize("feld", ["fill_id", "order_id", "symbol"])
def test_unvollstaendige_identitaet_wird_abgewiesen(feld: str) -> None:
    r = build_close_evidence(_close(**{feld: ""}), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert r.status is CollectionStatus.CLOSE_IDENTITY_INCOMPLETE
    assert feld in r.detail


def test_venue_muss_explizit_sein() -> None:
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


def test_naive_sammelzeit_wird_abgewiesen() -> None:
    """Sonst haengt `.timestamp()` still an der Maschinen-Zeitzone."""
    r = build_close_evidence(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=datetime(2026, 8, 21, 9, 5, 0)
    )
    assert r.status is CollectionStatus.INVALID_COLLECTION_TIME


def test_alle_identitaeten_landen_im_artefakt() -> None:
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    ev = r.evidence
    assert ev is not None
    assert (ev.close_fill_id, ev.close_order_id, ev.symbol, ev.venue) == (
        "fill_abc",
        "ord_abc",
        "ETH/USDT",
        "bybit",
    )
    assert ev.close_timestamp_utc == CLOSE_TS


# --- Fenster: fest, nicht konfigurierbar ----------------------------------------


def test_primaerfenster_ist_fest_und_eng() -> None:
    calls: list[dict] = []
    build_close_evidence(_close(), venue="bybit", fetch=_fetcher(record=calls), now_utc=NOW)
    assert len(calls) == 1
    call = calls[0]
    assert call["interval"] == PRIMARY_INTERVAL == "1m"
    assert call["end_ms"] - call["start_ms"] == 2 * PRIMARY_WINDOW_RADIUS_MINUTES * MINUTE
    assert call["start_ms"] < CLOSE_MS < call["end_ms"]


def test_das_fenster_laesst_sich_vom_aufrufer_nicht_verbreitern() -> None:
    """Sonst haette das Argument 'ein weites Fenster macht alles plausibel' keinen Halt."""
    import inspect as _inspect

    params = _inspect.signature(build_close_evidence).parameters
    assert "interval" not in params
    assert "window_minutes" not in params
    assert "window_radius_minutes" not in params
    with pytest.raises(TypeError):
        build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, interval="1h")


def test_radius_name_sagt_was_passiert() -> None:
    """Der Vorgaengername versprach drei Minuten und lieferte zwei."""
    assert PRIMARY_WINDOW_RADIUS_MINUTES == 1


# --- Kerzen-Rohdaten -------------------------------------------------------------


def test_leeres_fenster_ist_fehlende_evidenz() -> None:
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher([]), now_utc=NOW)
    assert r.status is CollectionStatus.WINDOW_UNAVAILABLE
    assert r.evidence is None


def test_fetch_fehler_ist_ein_sammelausfall_keine_exception() -> None:
    r = build_close_evidence(
        _close(), venue="bybit", fetch=_fetcher(raises=TimeoutError("kein Netz")), now_utc=NOW
    )
    assert r.status is CollectionStatus.FETCH_FAILED
    assert "TimeoutError" in r.detail


def test_kerzen_hinter_dem_sammelzeitpunkt_werden_abgewiesen() -> None:
    future = VenueCandle(int((NOW + timedelta(minutes=5)).timestamp() * 1000), 1.0, 2.0, 0.5, 1.5)
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher([future]), now_utc=NOW)
    assert r.status is CollectionStatus.CANDLES_IN_FUTURE


def test_noch_offene_kerze_taugt_nicht_als_evidenz() -> None:
    """High/Low/Close stehen erst fest, wenn das Intervall abgelaufen ist."""
    now = datetime.fromtimestamp((M0 + 30_000) / 1000, UTC)  # mitten in der Kerze
    r = build_close_evidence(
        _close(),
        venue="bybit",
        fetch=_fetcher([VenueCandle(M0, 100.0, 101.0, 99.5, 100.8)]),
        now_utc=now,
    )
    assert r.status is CollectionStatus.UNSETTLED_CANDLE


@pytest.mark.parametrize(
    "candle",
    [
        VenueCandle(M0, float("nan"), 101.0, 99.5, 100.8),
        VenueCandle(M0, 100.0, float("inf"), 99.5, 100.8),
        VenueCandle(M0, 100.0, 101.0, -1.0, 100.8),
        VenueCandle(M0, 0.0, 101.0, 99.5, 100.8),
        VenueCandle(M0, 105.0, 101.0, 99.5, 100.8),  # open > high
        VenueCandle(M0, 100.0, 101.0, 99.5, 150.0),  # close > high
    ],
)
def test_unbrauchbare_ohlc_werte_werden_als_status_gemeldet(candle: VenueCandle) -> None:
    """NaN darf nicht erst beim Hashen als Exception herausfallen."""
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher([candle]), now_utc=NOW)
    assert r.status is CollectionStatus.INVALID_CANDLE_DATA


def test_doppelte_kerzenzeit_wird_gemeldet() -> None:
    doppelt = [VenueCandle(M0, 100.0, 101.0, 99.5, 100.8)] * 2
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(doppelt), now_utc=NOW)
    assert r.status is CollectionStatus.INVALID_CANDLE_DATA


def test_kerzen_werden_nach_zeit_sortiert() -> None:
    r = build_close_evidence(
        _close(), venue="bybit", fetch=_fetcher(list(reversed(_default_candles()))), now_utc=NOW
    )
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


# --- Pfadsicherheit --------------------------------------------------------------


def test_fill_id_landet_nie_als_pfadsegment(tmp_path: Path) -> None:
    """`../../foo` darf niemals einen Schreibzugriff ausserhalb erzeugen."""
    boese = _close(fill_id="../../pwned")
    built = build_close_evidence(boese, venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    r = publish_evidence(built.evidence, tmp_path)

    assert r.status is CollectionStatus.COLLECTED
    written = Path(r.path).resolve()
    assert tmp_path.resolve() in written.parents
    assert "pwned" not in str(written)
    # Die echte fill_id bleibt im Manifest erhalten.
    manifest = json.loads((_the_folder(tmp_path) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fill_id"] == "../../pwned"


# --- Veröffentlichung: atomar, verankert, idempotent -----------------------------


def test_artefakt_wird_unter_seinem_hash_abgelegt(tmp_path: Path) -> None:
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    r = publish_evidence(built.evidence, tmp_path)

    assert r.status is CollectionStatus.COLLECTED
    path = Path(r.path)
    assert path.name == f"{built.payload_sha256}.json"
    assert path.read_bytes() == canonical_bytes(built.evidence.as_payload())


def test_manifest_verankert_den_hash(tmp_path: Path) -> None:
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    publish_evidence(built.evidence, tmp_path)

    manifest = json.loads((_the_folder(tmp_path) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["payload_sha256"] == built.payload_sha256
    assert manifest["fill_id"] == "fill_abc"
    assert manifest["order_id"] == "ord_abc"
    assert manifest["venue"] == "bybit"


def test_abweichende_evidenz_ist_ein_konflikt_und_ueberschreibt_nichts(tmp_path: Path) -> None:
    first = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert first.evidence is not None
    publish_evidence(first.evidence, tmp_path)

    geaendert = [VenueCandle(M0, 100.0, 200.0, 99.5, 100.8)]
    second = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(geaendert), now_utc=NOW)
    assert second.evidence is not None
    conflict = publish_evidence(second.evidence, tmp_path)

    assert conflict.status is CollectionStatus.EVIDENCE_CONFLICT
    assert first.payload_sha256 in conflict.detail
    folder = _the_folder(tmp_path)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["payload_sha256"] == first.payload_sha256
    assert not (folder / f"{second.payload_sha256}.json").exists()


def test_artefakt_ohne_manifest_ist_ein_befund(tmp_path: Path) -> None:
    """Crash zwischen den beiden Schreibvorgaengen — kein stilles Neuanlegen."""
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    publish_evidence(built.evidence, tmp_path)

    folder = _the_folder(tmp_path)
    (folder / "manifest.json").unlink()  # Commit-Marker weg = nicht veroeffentlicht

    again = publish_evidence(built.evidence, tmp_path)
    assert again.status is CollectionStatus.UNANCHORED_ARTIFACT_PRESENT


def test_unlesbares_manifest_ist_ein_konflikt(tmp_path: Path) -> None:
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    publish_evidence(built.evidence, tmp_path)
    (_the_folder(tmp_path) / "manifest.json").write_text("{kaputt", encoding="utf-8")

    r = publish_evidence(built.evidence, tmp_path)
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT


def test_paralleler_schreiber_wird_abgewiesen(tmp_path: Path) -> None:
    """Zwei Sammler duerfen nicht beide 'kein Manifest' sehen und losschreiben."""
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None

    from app.execution.close_evidence_collector import _folder_key

    folder = tmp_path / _folder_key("fill_abc")
    folder.mkdir(parents=True)
    (folder / ".publish.lock").touch()  # ein anderer Sammler haelt den Platz

    r = publish_evidence(built.evidence, tmp_path)
    assert r.status is CollectionStatus.CONCURRENT_WRITER


def test_keine_temporaerdateien_und_kein_lock_bleiben_zurueck(tmp_path: Path) -> None:
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    publish_evidence(built.evidence, tmp_path)
    folder = _the_folder(tmp_path)
    assert not list(folder.glob("*.tmp"))
    assert not (folder / ".publish.lock").exists()


# --- Echte Retry-Idempotenz ------------------------------------------------------


def test_retry_zehn_sekunden_spaeter_ist_ein_noop(tmp_path: Path) -> None:
    """DER Fall: identische Kerzen, aber neues collected_at ⇒ anderer Hash.

    Wer erst sammelt und dann veroeffentlicht, erzeugt hier einen
    EVIDENCE_CONFLICT aus dem Nichts. Der orchestrierende Pfad prueft deshalb
    ZUERST das Manifest.
    """
    first = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert first.status is CollectionStatus.COLLECTED

    calls: list[dict] = []
    second = collect_and_publish(
        _close(),
        venue="bybit",
        fetch=_fetcher(record=calls),
        now_utc=NOW + timedelta(seconds=10),
        base_dir=tmp_path,
    )
    assert second.status is CollectionStatus.IDEMPOTENT_NOOP
    assert second.payload_sha256 == first.payload_sha256
    assert calls == [], "es darf gar nicht erst erneut abgerufen werden"


def test_ohne_orchestrierung_wuerde_der_retry_kollidieren(tmp_path: Path) -> None:
    """Belegt, warum der Vorpruef-Pfad noetig ist — nicht nur behauptet."""
    first = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert first.evidence is not None
    publish_evidence(first.evidence, tmp_path)

    later = build_close_evidence(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW + timedelta(seconds=10)
    )
    assert later.evidence is not None
    assert later.payload_sha256 != first.payload_sha256  # nur wegen collected_at
    assert publish_evidence(later.evidence, tmp_path).status is CollectionStatus.EVIDENCE_CONFLICT


def test_orchestrierung_meldet_fremde_identitaet_im_manifest(tmp_path: Path) -> None:
    collect_and_publish(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path)
    folder = _the_folder(tmp_path)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["order_id"] = "ord_fremd"
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT


def test_orchestrierung_meldet_fehlendes_artefakt(tmp_path: Path) -> None:
    first = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    Path(first.path).unlink()

    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.UNANCHORED_ARTIFACT_PRESENT


def test_der_verifier_akzeptiert_das_veroeffentlichte_artefakt(tmp_path: Path) -> None:
    """Ende-zu-Ende: der verankerte Hash ist der, den der Verifier verlangt."""
    from app.execution.close_verifier import ReasonCode, verify_close

    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    published = publish_evidence(built.evidence, tmp_path)

    result = verify_close(
        _close(), built.evidence, expected_evidence_sha256=published.payload_sha256
    )
    for code in (
        ReasonCode.MISSING_EVIDENCE_HASH,
        ReasonCode.EVIDENCE_HASH_MISMATCH,
        ReasonCode.EVIDENCE_ORDER_MISMATCH,
        ReasonCode.EVIDENCE_CLOSE_TIME_MISMATCH,
    ):
        assert code not in result.reasons
