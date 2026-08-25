"""Der eingefrorene OOS-Datenschnitt — Bytes, nicht "was der Provider gerade sagt".

Ohne dieses Artefakt haette ein Absturz zwischen ``EVALUATE`` und dem fertigen
p-Wert eine haessliche Folge: beim Neustart wuerde erneut vom Provider geladen.
Kaeme dann eine minimal andere Historie zurueck — eine nachgelieferte Kerze, ein
korrigiertes Volumen — waere die Wiederholung nicht mehr dieselbe Auswertung,
sondern eine zweite. Genau davor schuetzt ein Hash ueber die exakten Daten.

Was hier NICHT hineingehoert, ist so wichtig wie der Inhalt::

    kein mean, kein p-Wert, kein Verdikt   -> das ist Ergebnis, nicht Eingabe
    kein frozen_at_utc, kein Pfad, keine PID
                                           -> ein Retry wuerde sonst denselben
                                              Datensatz anders hashen

**DATA_UNAVAILABLE bleibt sichtbar.** Ein fehlendes Label ist ``None`` und wird
als ``null`` serialisiert — niemals ``0.0``. Eine Null ist eine Beobachtung
("die Bewegung war null"), ein ``None`` ist die Abwesenheit einer Beobachtung.
Wer beides zusammenwirft, faelscht den Mittelwert nach unten und ``n_valid``
nach oben.

**NaN und Unendlich sind hier gar nicht erst serialisierbar.** ``json.dumps``
schreibt sie sonst als ``NaN``/``Infinity`` — kein gueltiges JSON, aber
schlimmer: sie kaemen durch jeden ``is not None``-Guard, weil ``NaN is not None``
``True`` ist. Der Serialisierer bricht ab.

**Das Fenster ist eine Aussage ueber Beobachtbarkeit, nicht ueber Zeitstempel.**
Ein Signal gehoert nur dann in den Checkpoint, wenn sein Label bis dahin
vollstaendig beobachtbar WAR::

    signal_time >= T0   UND   label_exit_time <= cutoff

Deshalb traegt jede Zeile ihren ``label_exit_utc`` mit. Spaeter laesst sich damit
unabhaengig beweisen, dass dieses Label am Checkpoint bereits bekannt sein
konnte — statt dem Code zu glauben, es habe zum Fenster gehoert.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

DATASET_SCHEMA_VERSION = "kai/prereg-evaluation-dataset/v2"

# Mindestabdeckung des OOS-Fensters je Symbol.
#
# Ohne sie bliebe die Frozen-Grenze eine Absichtserklaerung: ``build_frozen_dataset``
# bekommt Zeilen von einem Lader und kann nicht beweisen, dass es der VOLLSTAENDIGE
# Schnitt war. Ein Lader, der (versehentlich oder nicht) nur die feuernden Zeilen
# liefert, laege bei ~0,2 % Abdeckung — die Regel feuert rund 1,5-mal pro Tag ueber
# 34 Symbole, gegenueber 24 Kerzen pro Symbol und Tag. Ein echter Provider-Ausfall
# liegt bei 99,x %.
#
# 0.95 trennt beides mit grossem Abstand und toleriert reale Luecken. Die
# gemessenen Werte wandern in den Datensatz und damit in ``dataset_sha256`` —
# eine Luecke ist danach sichtbar, nicht stillschweigend akzeptiert.
MIN_BAR_COVERAGE = 0.95


class FrozenDatasetError(ValueError):
    """Der Datenschnitt ist nicht einfrierbar — fail-closed vor dem Hashen."""


@dataclass(frozen=True)
class FrozenRow:
    """Eine auswertbare Signalzeile. Merkmale, Label, und WANN das Label feststand.

    ``features`` enthaelt ausschliesslich, was zum Signalzeitpunkt bekannt war.
    ``label_bps`` ist ``None``, wenn das Ergebnis nicht beobachtbar war —
    DATA_UNAVAILABLE, ausdruecklich kein Nullsignal.
    """

    signal_timestamp_utc: str
    label_exit_utc: str
    features: dict[str, float | None]
    label_bps: float | None


@dataclass(frozen=True)
class SymbolCoverage:
    """Wie vollstaendig der Schnitt dieses Symbols war.

    Geht in ``dataset_sha256`` ein: die Abdeckung ist Teil dessen, WAS
    eingefroren wurde, nicht eine Randnotiz darueber.
    """

    bars_expected: int
    bars_present: int

    @property
    def ratio(self) -> float:
        return self.bars_present / self.bars_expected if self.bars_expected else 1.0


@dataclass(frozen=True)
class FrozenSymbolPanel:
    """Alle eingefrorenen Zeilen eines kanonischen Symbols."""

    symbol: str
    rows: tuple[FrozenRow, ...]
    coverage: SymbolCoverage = SymbolCoverage(0, 0)


@dataclass(frozen=True)
class FrozenEvaluationDataset:
    """Der Datenschnitt eines Checkpoints. Unveraenderlich und hashbar."""

    schema_version: str
    checkpoint: str
    t0_utc: str
    cutoff_utc: str
    symbols: tuple[str, ...]
    panels: tuple[FrozenSymbolPanel, ...]

    @property
    def n_rows(self) -> int:
        return sum(len(panel.rows) for panel in self.panels)


def _require_utc(timestamp_utc: str, field: str) -> str:
    """Zeitzonenbehaftet und auf UTC normiert. Naiv wird abgelehnt, nicht geraten.

    Ein naiver Zeitstempel still als UTC zu lesen ist die Sorte Annahme, die
    genau dann falsch ist, wenn sie teuer wird.
    """
    if not isinstance(timestamp_utc, str) or not timestamp_utc:
        raise FrozenDatasetError(f"{field}: kein Zeitstempel")
    try:
        parsed = datetime.fromisoformat(timestamp_utc)
    except ValueError as exc:
        raise FrozenDatasetError(f"{field}: {timestamp_utc!r} ist kein ISO-8601") from exc
    if parsed.tzinfo is None:
        raise FrozenDatasetError(
            f"{field}: {timestamp_utc!r} ist zeitzonenlos — UTC wird nicht geraten"
        )
    return parsed.astimezone(UTC).isoformat()


def _to_epoch_ms(timestamp_utc: str) -> int:
    return int(datetime.fromisoformat(timestamp_utc).timestamp() * 1000)


def _finite_or_none(value: object, where: str) -> float | None:
    """``None`` bleibt ``None``; alles andere muss ein endlicher ``float`` sein.

    ``bool`` wird abgewiesen: es ist eine Unterklasse von ``int`` und wuerde
    sonst als 0.0/1.0 durchgehen.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrozenDatasetError(f"{where}: {type(value).__name__} ist kein Messwert")
    number = float(value)
    if not math.isfinite(number):
        raise FrozenDatasetError(
            f"{where}: {number!r} ist nicht endlich. NaN passiert jeden "
            "`is not None`-Guard und propagiert lautlos durch jede Aggregation."
        )
    return number


def build_frozen_dataset(
    *,
    checkpoint: str,
    t0_utc: str,
    cutoff_utc: str,
    sealed_symbols: Sequence[str],
    rows_by_symbol: Mapping[str, Sequence[FrozenRow]],
    timeframe_ms: int,
    horizon: int,
    min_coverage: float = MIN_BAR_COVERAGE,
) -> FrozenEvaluationDataset:
    """Friere den Datenschnitt ein. Reihenfolge und Mitgliedschaft sind gebunden.

    Args:
        checkpoint: ``T1`` oder ``T2``.
        t0_utc: Beginn des OOS-Fensters (zeitzonenbehaftet).
        cutoff_utc: der Checkpoint. Ein Label zaehlt nur, wenn es bis hierhin
            vollstaendig beobachtbar war.
        sealed_symbols: die kanonische Universumsliste IN IHRER REIHENFOLGE.
        rows_by_symbol: je Symbol die Kandidatenzeilen (ungefiltert). Erwartet
            wird der VOLLSTAENDIGE Schnitt, nicht nur die feuernden Zeilen — das
            wird ueber die Abdeckung mechanisch geprueft.
        timeframe_ms: Kerzenlaenge; bestimmt, wie viele Kerzen das Fenster hat.
        horizon: Haltedauer in Kerzen; die letzten ``horizon`` Kerzen koennen
            kein vollstaendig beobachtetes Label mehr tragen und zaehlen nicht
            zur Erwartung.
        min_coverage: Mindestanteil vorhandener Kerzen je Symbol.

    Returns:
        FrozenEvaluationDataset mit genau ``len(sealed_symbols)`` Panels — auch
        fuer Symbole ohne eine einzige gueltige Zeile.

    Raises:
        FrozenDatasetError: Symbolmenge weicht ab, Duplikat, Zeitfenster
            verletzt, ein Wert ist nicht endlich, oder die Abdeckung eines
            Symbols liegt unter ``min_coverage`` — dann war es nicht der
            vollstaendige Schnitt.
    """
    if checkpoint not in {"T1", "T2"}:
        raise FrozenDatasetError(f"checkpoint {checkpoint!r} ist kein Entscheidungszeitpunkt")

    symbols = tuple(sealed_symbols)
    if len(set(symbols)) != len(symbols):
        raise FrozenDatasetError("das versiegelte Universum enthaelt ein Duplikat")

    extras = sorted(set(rows_by_symbol) - set(symbols))
    if extras:
        raise FrozenDatasetError(
            f"Symbole ausserhalb des versiegelten Universums: {extras}. "
            "Das Universum ist unveraenderlich; nichts rueckt nach."
        )

    start = _require_utc(t0_utc, "t0_utc")
    cutoff = _require_utc(cutoff_utc, "cutoff_utc")
    if cutoff <= start:
        raise FrozenDatasetError("cutoff_utc liegt nicht nach t0_utc")

    if timeframe_ms <= 0:
        raise FrozenDatasetError("timeframe_ms muss > 0 sein")
    if horizon < 1:
        raise FrozenDatasetError("horizon muss >= 1 sein")

    # Die letzten ``horizon`` Kerzen koennen kein vollstaendig beobachtetes
    # Label mehr tragen; sie gehoeren nicht in die Erwartung.
    window_ms = _to_epoch_ms(cutoff) - _to_epoch_ms(start)
    bars_expected = max(0, window_ms // timeframe_ms - horizon)

    panels: list[FrozenSymbolPanel] = []
    for symbol in symbols:
        frozen_rows: list[FrozenRow] = []
        for index, row in enumerate(rows_by_symbol.get(symbol, ())):
            where = f"{symbol}[{index}]"
            signal_at = _require_utc(row.signal_timestamp_utc, f"{where}.signal_timestamp_utc")
            exit_at = _require_utc(row.label_exit_utc, f"{where}.label_exit_utc")
            if exit_at <= signal_at:
                raise FrozenDatasetError(f"{where}: label_exit_utc liegt nicht nach dem Signal")
            # Das Fenster ist eine Aussage ueber Beobachtbarkeit: das Label muss
            # bis zum Checkpoint VOLLSTAENDIG vorgelegen haben, nicht nur das
            # Signal.
            if signal_at < start or exit_at > cutoff:
                continue
            frozen_rows.append(
                FrozenRow(
                    signal_timestamp_utc=signal_at,
                    label_exit_utc=exit_at,
                    features={
                        name: _finite_or_none(value, f"{where}.features[{name!r}]")
                        for name, value in sorted(row.features.items())
                    },
                    label_bps=_finite_or_none(row.label_bps, f"{where}.label_bps"),
                )
            )
        frozen_rows.sort(key=lambda r: (r.signal_timestamp_utc, r.label_exit_utc))

        # Die Frozen-Grenze mechanisch sichern: erwartet wird der VOLLSTAENDIGE
        # Schnitt. Ein Lader, der nur feuernde Zeilen liefert, faellt hier auf —
        # er laege um Groessenordnungen unter der Schranke.
        coverage = SymbolCoverage(
            bars_expected=bars_expected,
            bars_present=len({row.signal_timestamp_utc for row in frozen_rows}),
        )
        if bars_expected and coverage.ratio < min_coverage:
            raise FrozenDatasetError(
                f"{symbol}: {coverage.bars_present} von {bars_expected} Kerzen "
                f"({coverage.ratio:.1%}) — unter {min_coverage:.0%}. Erwartet wird "
                "der vollstaendige OOS-Schnitt, nicht nur die feuernden Zeilen."
            )

        # Auch ein Symbol ohne gueltige Zeile bleibt Mitglied: DATA_UNAVAILABLE
        # ist NICHT dasselbe wie "Asset entfernt".
        panels.append(FrozenSymbolPanel(symbol=symbol, rows=tuple(frozen_rows), coverage=coverage))

    return FrozenEvaluationDataset(
        schema_version=DATASET_SCHEMA_VERSION,
        checkpoint=checkpoint,
        t0_utc=start,
        cutoff_utc=cutoff,
        symbols=symbols,
        panels=tuple(panels),
    )


def dataset_to_dict(dataset: FrozenEvaluationDataset) -> dict[str, Any]:
    """Serialisierbare Form. Enthaelt bewusst KEINE Laufzeit-Metadaten."""
    return {
        "schema_version": dataset.schema_version,
        "checkpoint": dataset.checkpoint,
        "t0_utc": dataset.t0_utc,
        "cutoff_utc": dataset.cutoff_utc,
        "symbols": list(dataset.symbols),
        "panels": [
            {
                "symbol": panel.symbol,
                "coverage": {
                    "bars_expected": panel.coverage.bars_expected,
                    "bars_present": panel.coverage.bars_present,
                },
                "rows": [
                    {
                        "signal_timestamp_utc": row.signal_timestamp_utc,
                        "label_exit_utc": row.label_exit_utc,
                        "features": dict(sorted(row.features.items())),
                        "label_bps": row.label_bps,
                    }
                    for row in panel.rows
                ],
            }
            for panel in dataset.panels
        ],
    }


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Eine Byte-Folge je Inhalt — und ein Abbruch bei NaN/Infinity.

    ``allow_nan=False`` ist hier nicht Kosmetik: ohne das schriebe ``json.dumps``
    ``NaN`` bzw. ``Infinity``, was weder gueltiges JSON ist noch beim
    Zurueckladen als Defekt auffiele.
    """
    body = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return body.encode("utf-8")


def dataset_sha256(dataset: FrozenEvaluationDataset) -> str:
    """Hash ueber die exakten Daten — Reihenfolge und Fehlwerte eingeschlossen."""
    return hashlib.sha256(canonical_bytes(dataset_to_dict(dataset))).hexdigest()
