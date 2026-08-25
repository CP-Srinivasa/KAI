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


@pytest.mark.parametrize("bad", ["kaputt", "2026-08-21T09:00:30", "2026-08-21T11:00:30+02:00"])
def test_unbrauchbare_close_zeit_wird_abgewiesen(bad: str) -> None:
    """Naiv ist Raten — und ein Feld namens `_utc` darf keinen Offset +02:00 tragen.

    Nicht still umrechnen: der Verifier vergleicht den Evidence-Timestamp mit dem
    Close-Timestamp als exakten String.
    """
    r = build_close_evidence(
        _close(timestamp_utc=bad), venue="bybit", fetch=_fetcher(), now_utc=NOW
    )
    assert r.status is CollectionStatus.INVALID_CLOSE_TIMESTAMP


def test_fehlende_close_zeit_ist_unvollstaendige_identitaet() -> None:
    """Fehlend und unbrauchbar sind zwei verschiedene Wahrheiten."""
    r = build_close_evidence(_close(timestamp_utc=""), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert r.status is CollectionStatus.CLOSE_IDENTITY_INCOMPLETE
    assert "timestamp_utc" in r.detail


def test_utc_mit_z_suffix_wird_akzeptiert() -> None:
    r = build_close_evidence(
        _close(timestamp_utc="2026-08-21T09:00:30Z"), venue="bybit", fetch=_fetcher(), now_utc=NOW
    )
    assert r.status is CollectionStatus.COLLECTED


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


def test_primaerfenster_haengt_an_candle_buckets() -> None:
    """Grenzen exakt gepinnt — nicht "ungefaehr um den Close herum".

    Viele Kline-APIs filtern nach Candle-OPEN-Zeit. Ein Fenster
    08:59:30..09:01:30 laesst die 08:59-Kerze deshalb je nach Anbieter heraus,
    obwohl "eine davor" behauptet wird. Gerechnet wird deshalb ueber den Bucket.
    """
    calls: list[dict] = []
    build_close_evidence(_close(), venue="bybit", fetch=_fetcher(record=calls), now_utc=NOW)
    assert len(calls) == 1
    call = calls[0]
    assert call["interval"] == PRIMARY_INTERVAL == "1m"
    # Close 09:00:30 -> Bucket 09:00:00; davor 08:59:00, danach bis 09:02:00.
    assert call["start_ms"] == M_PREV
    assert call["end_ms"] == M0 + 2 * MINUTE
    assert call["start_ms"] < CLOSE_MS < call["end_ms"]
    assert call["end_ms"] - call["start_ms"] == 3 * MINUTE


def test_fenstergrenzen_liegen_auf_minutengrenzen() -> None:
    calls: list[dict] = []
    build_close_evidence(_close(), venue="bybit", fetch=_fetcher(record=calls), now_utc=NOW)
    call = calls[0]
    assert call["start_ms"] % MINUTE == 0
    assert call["end_ms"] % MINUTE == 0


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
    """Kerze IM Fenster, aber nach dem Sammelzeitpunkt — sonst greift die
    Fenster-Pruefung zuerst und der Zukunftsfall bliebe ungeprueft."""
    frueh = datetime.fromtimestamp((M0 - 30_000) / 1000, UTC)  # 08:59:30
    r = build_close_evidence(
        _close(),
        venue="bybit",
        fetch=_fetcher([VenueCandle(M0, 100.0, 101.0, 99.5, 100.8)]),
        now_utc=frueh,
    )
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


def test_liegendes_lock_haelt_fail_closed(tmp_path: Path) -> None:
    """Der Status behauptet nur, was bekannt ist: ein Lock LIEGT.

    Ob dahinter ein lebender Schreiber steht oder ein verwaistes Lock nach
    Stromausfall, ist nicht beweisbar — deshalb PUBLISH_LOCK_PRESENT und
    ausdruecklich keine "stale lock nach X Minuten loeschen"-Heuristik.
    """
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None

    from app.execution.close_evidence_collector import _folder_key

    folder = tmp_path / _folder_key("fill_abc")
    folder.mkdir(parents=True)
    (folder / ".publish.lock").touch()

    r = publish_evidence(built.evidence, tmp_path)
    assert r.status is CollectionStatus.PUBLISH_LOCK_PRESENT
    assert "verwaistes" in r.detail


def test_orchestrator_ruft_hinter_dem_lock_gar_nicht_erst_ab(tmp_path: Path) -> None:
    """Sonst starten zwei Laeufe beide einen Netzabruf und kollidieren danach."""
    from app.execution.close_evidence_collector import _folder_key

    folder = tmp_path / _folder_key("fill_abc")
    folder.mkdir(parents=True)
    (folder / ".publish.lock").touch()

    calls: list[dict] = []
    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(record=calls), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.PUBLISH_LOCK_PRESENT
    assert calls == []


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


# --- Venue gehoert zur Identitaet ------------------------------------------------


def test_retry_mit_anderer_venue_bekommt_keine_fremde_evidenz(tmp_path: Path) -> None:
    """Sonst laege Bybit-Evidenz als Antwort auf eine Binance-Anfrage vor."""
    first = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert first.status is CollectionStatus.COLLECTED

    r = collect_and_publish(
        _close(), venue="binance", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT


def test_retry_ohne_venue_bekommt_keine_evidenz(tmp_path: Path) -> None:
    collect_and_publish(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path)
    r = collect_and_publish(_close(), venue="", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path)
    assert r.status is CollectionStatus.CLOSE_IDENTITY_INCOMPLETE
    assert "venue" in r.detail


def test_venue_wird_kanonisiert(tmp_path: Path) -> None:
    """`Bybit `, `BYBIT` und `bybit` meinen dieselbe Venue — auch beim Retry."""
    first = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    second = collect_and_publish(
        _close(), venue="  BYBIT ", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert second.status is CollectionStatus.IDEMPOTENT_NOOP
    assert second.payload_sha256 == first.payload_sha256


# --- Der Anker wird geprueft, nicht geglaubt -------------------------------------


def _corrupt_manifest(tmp_path: Path, **changes: object) -> None:
    folder = _the_folder(tmp_path)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(changes)
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_manifest_mit_unbrauchbarem_hash_wird_abgewiesen(tmp_path: Path) -> None:
    """Der Wert bildet einen Dateinamen — ein manipuliertes Manifest darf keinen
    Pfad beeinflussen."""
    collect_and_publish(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path)
    _corrupt_manifest(tmp_path, payload_sha256="../../etc/passwd")

    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT
    assert "64" in r.detail


@pytest.mark.parametrize("feld", ["fill_id", "order_id", "symbol", "venue", "schema_version"])
def test_unvollstaendiges_manifest_wird_abgewiesen(tmp_path: Path, feld: str) -> None:
    collect_and_publish(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path)
    _corrupt_manifest(tmp_path, **{feld: ""})

    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT
    assert feld in r.detail


def test_veraenderte_artefakt_bytes_werden_erkannt(tmp_path: Path) -> None:
    """Der Hash wird ueber die TATSAECHLICHEN Bytes nachgerechnet."""
    first = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    Path(first.path).write_text('{"manipuliert":true}', encoding="utf-8")

    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT
    assert "Bytes" in r.detail


def test_manifest_und_artefakt_muessen_denselben_close_meinen(tmp_path: Path) -> None:
    collect_and_publish(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path)
    _corrupt_manifest(tmp_path, order_id="ord_fremd")

    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT


def test_publish_repariert_ein_fehlendes_artefakt_nicht_still(tmp_path: Path) -> None:
    """Auch der Low-Level-Pfad: Manifest ohne Artefakt ist ein Befund."""
    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    first = publish_evidence(built.evidence, tmp_path)
    Path(first.path).unlink()

    again = publish_evidence(built.evidence, tmp_path)
    assert again.status is CollectionStatus.UNANCHORED_ARTIFACT_PRESENT


# --- Kerzen-Typen sind fail-closed ----------------------------------------------


def test_bool_ist_kein_gueltiger_ohlc_wert() -> None:
    """bool ist eine int-Unterklasse — dieselbe Falle wie im Verifier.

    Die Werte sind bewusst so gewaehlt, dass ``True`` (== 1) die
    OHLC-Konsistenz NICHT verletzt: low 0.5 <= 1 <= high 2.0. Sonst faenge der
    Test ueber den Konsistenzpfad und der bool-Ausschluss bliebe ungeprueft —
    die Mutations-Gegenprobe hat genau das gezeigt.
    """
    r = build_close_evidence(
        _close(),
        venue="bybit",
        fetch=_fetcher([VenueCandle(M0, True, 2.0, 0.5, 1.5)]),
        now_utc=NOW,
    )
    assert r.status is CollectionStatus.INVALID_CANDLE_DATA
    assert "OHLC-Werte" in r.detail


@pytest.mark.parametrize("bad_time", [True, -1, "1000", 1.5, None])
def test_unbrauchbare_kerzenzeit_wird_gemeldet(bad_time: object) -> None:
    """Kein TypeError aus der Validierung — ein kaputter Datensatz ist ein Status."""
    r = build_close_evidence(
        _close(),
        venue="bybit",
        fetch=_fetcher([VenueCandle(bad_time, 100.0, 101.0, 99.5, 100.8)]),
        now_utc=NOW,
    )
    assert r.status is CollectionStatus.INVALID_CANDLE_DATA


def test_neuer_close_ordner_wird_im_parent_haltbar_gemacht(tmp_path: Path, monkeypatch) -> None:
    """Ohne Parent-fsync ueberlebt der Verzeichniseintrag keinen Stromausfall.

    Der Effekt ist nicht beobachtbar, der Aufruf schon — und ohne diesen Test
    ueberlebt das Entfernen des fsync die Suite unbemerkt.
    """
    import app.execution.close_evidence_collector as mod

    gefsynct: list[str] = []
    echt = mod._fsync_dir
    monkeypatch.setattr(
        mod, "_fsync_dir", lambda folder: (gefsynct.append(str(folder)), echt(folder))[1]
    )

    built = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW)
    assert built.evidence is not None
    publish_evidence(built.evidence, tmp_path)

    assert str(tmp_path) in gefsynct, "der Parent des neuen Close-Ordners wurde nicht gefsynct"
    assert any(str(_the_folder(tmp_path)) == p for p in gefsynct), "Close-Ordner nicht gefsynct"


# --- A: die zurueckgelieferten Kerzen muessen zum angefragten Fenster passen ------


def test_kerze_ausserhalb_des_angefragten_fensters_wird_abgewiesen() -> None:
    """Sonst behaupten window_start/window_end im Artefakt etwas anderes als sein Inhalt."""
    weit_davor = VenueCandle(M0 - 20 * MINUTE, 100.0, 101.0, 99.5, 100.8)
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher([weit_davor]), now_utc=NOW)
    assert r.status is CollectionStatus.INVALID_CANDLE_DATA
    assert "ausserhalb" in r.detail


def test_kerze_am_oberen_fensterrand_wird_abgewiesen() -> None:
    """Das Fenster ist halboffen: end_ms selbst gehoert nicht mehr dazu."""
    r = build_close_evidence(
        _close(),
        venue="bybit",
        fetch=_fetcher([VenueCandle(M0 + 2 * MINUTE, 100.0, 101.0, 99.5, 100.8)]),
        now_utc=NOW,
    )
    assert r.status is CollectionStatus.INVALID_CANDLE_DATA


def test_kerze_ohne_minutengrenze_wird_abgewiesen() -> None:
    """open_time mit Sekunden-Offset ist keine 1m-Kerze."""
    schief = VenueCandle(M0 + 30_000, 100.0, 101.0, 99.5, 100.8)
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher([schief]), now_utc=NOW)
    assert r.status is CollectionStatus.INVALID_CANDLE_DATA
    assert "Grenze" in r.detail


# --- B: der Anker wird auf Vollstaendigkeit und Kanonizitaet geprueft ------------


@pytest.mark.parametrize("feld", ["collector_code_sha", "collected_at_utc", "payload_sha256"])
def test_manifest_ohne_pflichtfeld_wird_abgewiesen(tmp_path: Path, feld: str) -> None:
    collect_and_publish(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path)
    _corrupt_manifest(tmp_path, **{feld: ""})

    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT


def test_fremde_schema_version_wird_abgewiesen(tmp_path: Path) -> None:
    """Fremde Version auf BEIDEN Seiten — sonst faenge der Metadaten-Abgleich.

    Die Mutations-Gegenprobe hat genau das gezeigt: aendert man nur das Manifest,
    schlaegt schon der Manifest-gegen-Artefakt-Vergleich an und die
    Schema-Version-Pruefung bliebe ungeprueft.
    """
    import hashlib

    first = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    folder = _the_folder(tmp_path)

    payload = json.loads(Path(first.path).read_text(encoding="utf-8"))
    payload["schema_version"] = "close_evidence/v99"
    roh = canonical_bytes(payload)
    neuer_sha = hashlib.sha256(roh).hexdigest()
    Path(first.path).unlink()
    (folder / f"{neuer_sha}.json").write_bytes(roh)
    _corrupt_manifest(tmp_path, schema_version="close_evidence/v99", payload_sha256=neuer_sha)

    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT
    assert "schema_version" in r.detail


def test_abweichende_schema_version_nur_im_manifest_faellt_ebenfalls_auf(
    tmp_path: Path,
) -> None:
    """Der andere Pfad — Manifest und Artefakt widersprechen sich."""
    collect_and_publish(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path)
    _corrupt_manifest(tmp_path, schema_version="close_evidence/v99")

    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT


@pytest.mark.parametrize("feld", ["collector_code_sha", "collected_at_utc"])
def test_manifest_und_artefakt_muessen_bei_metadaten_uebereinstimmen(
    tmp_path: Path, feld: str
) -> None:
    collect_and_publish(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path)
    _corrupt_manifest(tmp_path, **{feld: "abweichend"})

    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT
    assert feld in r.detail


def test_nichtkanonisches_artefakt_wird_erkannt(tmp_path: Path) -> None:
    """Der Writer schreibt IMMER kanonisch — alles andere stammt nicht von ihm.

    Hier wird der Inhalt NICHT veraendert, nur huebscher formatiert, und Manifest
    plus Dateiname werden passend nachgezogen. Ohne die Kanonizitaets-Pruefung
    wuerde das Artefakt akzeptiert.
    """
    import hashlib

    first = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    folder = _the_folder(tmp_path)
    payload = json.loads(Path(first.path).read_text(encoding="utf-8"))

    huebsch = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    neuer_sha = hashlib.sha256(huebsch).hexdigest()
    Path(first.path).unlink()
    (folder / f"{neuer_sha}.json").write_bytes(huebsch)
    _corrupt_manifest(tmp_path, payload_sha256=neuer_sha)

    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.EVIDENCE_CONFLICT
    assert "kanonische" in r.detail


# --- C: vollstaendige Identitaet vor dem Fast-Path -------------------------------


@pytest.mark.parametrize("feld", ["order_id", "symbol"])
def test_retry_ohne_vollstaendige_identitaet_meldet_das_richtige(tmp_path: Path, feld: str) -> None:
    """Sonst endete ein Retry mit fehlender order_id als EVIDENCE_CONFLICT."""
    collect_and_publish(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path)

    r = collect_and_publish(
        _close(**{feld: ""}), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.CLOSE_IDENTITY_INCOMPLETE
    assert feld in r.detail


def test_retry_mit_nicht_utc_zeit_meldet_den_zeitfehler(tmp_path: Path) -> None:
    collect_and_publish(_close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path)

    r = collect_and_publish(
        _close(timestamp_utc="2026-08-21T11:00:30+02:00"),
        venue="bybit",
        fetch=_fetcher(),
        now_utc=NOW,
        base_dir=tmp_path,
    )
    assert r.status is CollectionStatus.INVALID_CLOSE_TIMESTAMP


# --- Vollstaendigkeit: die Minute des Closes muss dabei sein ---------------------


def test_fenster_ohne_close_bucket_wird_abgewiesen() -> None:
    """Formal tadellos, inhaltlich unvollstaendig.

    Die 08:59-Kerze liegt im Fenster, ist ausgerichtet, settled und plausibel —
    aber sie deckt den Close um 09:00:30 nicht ab. Das ist Sammel-
    Unvollstaendigkeit und gehoert hier abgewiesen, nicht erst im Verifier.
    """
    nur_davor = [VenueCandle(M_PREV, 99.0, 100.5, 98.5, 100.0)]
    r = build_close_evidence(_close(), venue="bybit", fetch=_fetcher(nur_davor), now_utc=NOW)
    assert r.status is CollectionStatus.CLOSE_BUCKET_MISSING
    assert r.evidence is None


def test_fenster_ohne_close_bucket_ist_nicht_window_unavailable() -> None:
    """Zwei verschiedene Wahrheiten: keine Antwort vs. unvollstaendige Antwort."""
    leer = build_close_evidence(_close(), venue="bybit", fetch=_fetcher([]), now_utc=NOW)
    assert leer.status is CollectionStatus.WINDOW_UNAVAILABLE

    nur_danach = [VenueCandle(M0 + MINUTE, 100.0, 101.0, 99.5, 100.8)]
    unvollstaendig = build_close_evidence(
        _close(), venue="bybit", fetch=_fetcher(nur_danach), now_utc=NOW
    )
    assert unvollstaendig.status is CollectionStatus.CLOSE_BUCKET_MISSING


def test_close_bucket_allein_genuegt() -> None:
    """Nachbarkerzen sind willkommen, aber nicht Bedingung."""
    r = build_close_evidence(
        _close(),
        venue="bybit",
        fetch=_fetcher([VenueCandle(M0, 100.0, 101.0, 99.5, 100.8)]),
        now_utc=NOW,
    )
    assert r.status is CollectionStatus.COLLECTED


def test_orchestrator_veroeffentlicht_ohne_close_bucket_nichts(tmp_path: Path) -> None:
    nur_davor = [VenueCandle(M_PREV, 99.0, 100.5, 98.5, 100.0)]
    r = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(nur_davor), now_utc=NOW, base_dir=tmp_path
    )
    assert r.status is CollectionStatus.CLOSE_BUCKET_MISSING
    # Was zaehlt: es wurde keine Evidenz veroeffentlicht.
    assert not list(tmp_path.rglob("*.json"))
    # Und es bleibt auch kein leerer Ordner liegen, der wie ein angefangener
    # Commit aussieht.
    assert not list(tmp_path.iterdir())


def test_gescheiterter_lauf_laesst_kein_lock_zurueck(tmp_path: Path) -> None:
    collect_and_publish(
        _close(),
        venue="bybit",
        fetch=_fetcher(raises=TimeoutError("kein Netz")),
        now_utc=NOW,
        base_dir=tmp_path,
    )
    assert not list(tmp_path.rglob(".publish.lock"))


def test_aufraeumen_ruehrt_veroeffentlichte_evidenz_nicht_an(tmp_path: Path) -> None:
    """rmdir loescht nur leere Verzeichnisse — ein Artefakt bleibt liegen."""
    first = collect_and_publish(
        _close(), venue="bybit", fetch=_fetcher(), now_utc=NOW, base_dir=tmp_path
    )
    assert first.status is CollectionStatus.COLLECTED
    assert Path(first.path).exists()
    assert (_the_folder(tmp_path) / "manifest.json").exists()
