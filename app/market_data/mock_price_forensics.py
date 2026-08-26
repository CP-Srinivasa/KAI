"""Identify prices that were produced by the synthetic mock adapter.

DS-20260818-MOCK-EXIT. ``MockMarketDataAdapter`` is the LAST link of the live
``fallback`` provider chain (``APP_MARKET_DATA_PROVIDER=fallback``). It returns
``is_stale=False``/``freshness_seconds=0.0`` unconditionally, so on a tick where
every real venue failed, the position monitor received a *synthetic* price that
passed its stale-guard and closed positions against it.

The mock curve is fully deterministic given the symbol's phase:

    round(base + base * (amplitude_pct/100) * sin(phase / 1440 * 2*pi), 2)

``phase = hash(symbol) % 360`` is per-process randomized, so the phase of a past
incident is unknown — but the *candidate set* is only 360 values wide per symbol
and every value carries exactly two decimals. That makes recognition exact
rather than heuristic: we accept a price only on bit-identical equality with a
reconstructed candidate (optionally after undoing the paper fill slippage).

Verified against the two independently-forensicked incidents:
  * ``3259.9692`` = mock(ETH/USDT, phase 297) * (1 - 0.0005)  [DS-20260601]
  * ``3225.6863500000004`` = mock(ETH/USDT, phase 101) * (1 - 0.0005)  [2026-08-11/12]

Both reproduce bit-exactly, float artefacts included. This is a FORENSIC reader:
it never mutates the append-only audit — it lets aggregators exclude closes that
were priced off synthetic data (memory ``paper_audit_pnl_field_semantics``:
quarantine, never delete).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from app.market_data.mock_adapter import (
    _BASE_PRICES,
    _DEFAULT_AMPLITUDE_PCT,
    _DEFAULT_BASE_PRICE,
    _PRICE_DECIMALS,
    _SINE_PERIOD_MINUTES,
)

# Paper fill slippage as a fraction (settings default ``paper_slippage_pct=0.05``
# → 0.05/100). Passed explicitly by callers that know a different rate; NOT read
# from get_settings() here so the detector stays usable in hot read paths
# (memory feedback_get_settings_uncached_hot_loop).
DEFAULT_PAPER_SLIPPAGE_FRACTION: float = 0.0005

_PHASES = 360

_PRICE_TICK = 0.01
"""Rundung der Kurvenwerte — und damit die feinste unterscheidbare Preisstufe.

Sie definiert die Slots, gegen die die Kurven-Abdeckung gerechnet wird.
"""


@dataclass(frozen=True)
class MockPriceMatch:
    """A bit-exact match of a price against the mock adapter's curve."""

    symbol: str
    price: float
    mock_raw_price: float
    phase: int
    slippage_applied: float
    """0.0 = raw mock price; +f = buy-side fill (short close); -f = sell-side fill."""

    coverage: float = 0.0
    """Anteil der quotierbaren Preise im Mock-Band, den die Kurve besetzt.

    Direkt die Falsch-Positiv-Rate dieses Treffers, falls der echte Preis im
    Band liegt. Siehe ``_COVERAGE_DOC`` fuer die Messung ueber alle Symbole.
    """

    @property
    def reportable(self) -> bool:
        """Darf der Treffer ueberhaupt vorgelegt werden?"""
        return self.coverage < MAX_REPORTABLE_COVERAGE

    @property
    def strong_capable(self) -> bool:
        """Darf ein Slippage-Treffer hier als belastbare Evidenz gelten?"""
        return self.coverage < MAX_STRONG_COVERAGE


@lru_cache(maxsize=256)
def _mock_candidates(symbol: str, amplitude_pct: float) -> dict[float, int]:
    """Every price the mock can emit for ``symbol`` → the phase producing it.

    ``offset_minutes`` is 0 for ``get_ticker``/``get_market_data_point`` (the only
    paths that can drive a fill), so the phase alone spans the candidate set.

    DEGENERATE PHASES ARE EXCLUDED. At sin(t)≈0 the curve returns the base price
    itself, and at |sin(t)|≈1 it returns base*(1±amplitude) — round, structurally
    distinguished numbers (150.00, 102.00, 3200.00) that a legitimate quote or a
    test fixture hits by itself. Bit-exact equality only proves synthetic origin
    where the value carries the curve's own information; on those three points it
    proves nothing. Dropping them costs ~1% of detection coverage and removes the
    entire class of false positives — the conservative direction, matching this
    module's contract that an unprovable row is never dropped.
    """
    base = _BASE_PRICES.get(symbol, _DEFAULT_BASE_PRICE)
    amplitude = base * (amplitude_pct / 100)
    degenerate = {
        round(base, _PRICE_DECIMALS),
        round(base + amplitude, _PRICE_DECIMALS),
        round(base - amplitude, _PRICE_DECIMALS),
    }
    out: dict[float, int] = {}
    for phase in range(_PHASES):
        t = phase / _SINE_PERIOD_MINUTES * 2 * math.pi
        price = round(base + amplitude * math.sin(t), _PRICE_DECIMALS)
        if price in degenerate:
            continue
        # First phase wins — sin is not injective over [0,360), and the lower
        # phase is the one a fresh process is more likely to have produced.
        out.setdefault(price, phase)
    return out


MAX_STRONG_COVERAGE: float = 0.20
"""Bis hierher darf ein Slippage-Treffer als BELASTBARE Evidenz gelten.

Mitte der gemessenen Luecke 5,6 % (ETH) -> 32,3 % (SPY). 0,10 waere schon zu
nah an ETH gewesen (Abstand 0,044) — ein Wert, der bei der kleinsten
Kurvenaenderung kippt.
"""

MAX_REPORTABLE_COVERAGE: float = 0.60
"""Ab hier ist ein Treffer ein Muenzwurf und wird gar nicht mehr vorgelegt.

Mitte der gemessenen Luecke 40,9 % (BNB) -> 77,4 % (AAPL).
"""

_COVERAGE_DOC = """Kurven-Abdeckung: wie viel ein Treffer ueberhaupt aussagt.

Die Abdeckung ist der Anteil der quotierbaren Preise INNERHALB des Mock-Bandes,
den die Kurve besetzt — und damit direkt die Falsch-Positiv-Rate eines Treffers,
wenn der echte Preis in diesem Band liegt. Gemessen 2026-08-26 ueber alle
Mock-Symbole:

    (Default)    100      199 /    199 Slots = 100,0 %
    SOL/USDT     150      266 /    299       =  89,0 %
    AAPL         185      285 /    368       =  77,4 %
    --------------------------------------------- MAX_REPORTABLE_COVERAGE 60 %
    BNB/USDT     400      326 /    797       =  40,9 %
    MSFT         420      328 /    836       =  39,2 %
    SPY          520      334 /   1035       =  32,3 %
    ------------------------------------------------- MAX_STRONG_COVERAGE 20 %
    ETH/USDT    3200      355 /   6372       =   5,6 %
    BTC/USDT   65000      359 / 129433       =   0,3 %

Beide Schwellen liegen in einer GEMESSENEN Luecke der Verteilung, nicht auf
einem Datenpunkt (Lehre aus #732): 77,4 % -> 40,9 % trennt "Muenzwurf" von
"vorlegen", 32,3 % -> 5,6 % trennt "vorlegen" von "belastbar". Analytisch faellt
die Abdeckung mit ``360 / (2 x Basispreis)``; belastbar wird ein Symbol also ab
einem Basispreis von rund 900.

Live-Verkehr 2026-08-26 (3159 Fill-/Close-Zeilen): BTC 816, ETH 693 = belastbar;
BNB 43 = vorlegen; SOL 153 = Muenzwurf; AAPL/MSFT/SPY kommen nicht vor. Die
zweistufige Staffelung existiert, damit ein Symbol wie BNB nicht STILL aus der
Deckung faellt — binaer waere es kommentarlos verschwunden.
"""


@lru_cache(maxsize=256)
def _curve_coverage(symbol: str, amplitude_pct: float) -> float:
    """Anteil der 2-Dezimalstellen-Slots im Band, den die Kurve besetzt."""
    values = sorted(_mock_candidates(symbol, amplitude_pct))
    if len(values) < 2:
        return 1.0
    span = values[-1] - values[0]
    # Ganzzahlige Slot-Rechnung statt Float-Abstandsvergleich: bei Basis 100 ist
    # der mittlere Abstand exakt _PRICE_TICK, ein ``>``/``>=`` an dieser Kante
    # waere reine Rundungslotterie.
    slots = round(span / _PRICE_TICK) + 1
    return len(values) / slots if slots > 0 else 1.0


def _is_discriminating(symbol: str, amplitude_pct: float) -> bool:
    """Darf ein Treffer bei diesem Symbol ueberhaupt vorgelegt werden?"""
    return _curve_coverage(symbol, amplitude_pct) < MAX_REPORTABLE_COVERAGE


def match_mock_price(
    symbol: str,
    price: object,
    *,
    slippage_pct: float = DEFAULT_PAPER_SLIPPAGE_FRACTION,
    amplitude_pct: float = _DEFAULT_AMPLITUDE_PCT,
    include_raw: bool = False,
) -> MockPriceMatch | None:
    """Return the match when ``price`` is bit-exactly a mock-derived price.

    Prueft die beiden Slippage-Varianten (``*(1-s)`` Sell-Fill, ``*(1+s)``
    Buy-Fill). Nur Bit-Gleichheit zaehlt — eine echte Venue-Quote ist ein
    kontinuierlicher Float und trifft ``round(x, 2)`` mal Slippage-Faktor nicht.

    ``include_raw`` nimmt zusaetzlich den UNSKALIERTEN Kurvenwert auf. Default
    aus, weil ``paper_engine`` jede Quote vor dem Buchen mit ``(1 ± slippage)``
    multipliziert — ein roher Wert kann dort nicht als ``exit_price`` auftauchen,
    waehrend runde Platzhalter (``FB/USDT 101.00``) ihn staendig treffen (#728).

    Es gibt aber einen Pfad ohne Slippage: ``fill_at_signal_entry`` bucht Premium-
    Einstiege 1:1 zum Signalpreis (``fill_price_override``), und im Live-Audit
    tragen drei Fills ``slippage_pct: None``. Fuer einen SUCHENDEN Waechter ist
    dieser Zweig deshalb wertvoll — er soll lieber einen runden Preis zur
    Sichtpruefung vorlegen als einen synthetischen Fill uebersehen. Wer den Zweig
    einschaltet, muss den Treffer als SCHWAECHEREN Beleg behandeln:
    ``slippage_applied == 0.0`` weist ihn aus.
    """
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        return None
    value = float(price)
    if value <= 0 or value != value:  # non-positive or NaN
        return None
    sym = str(symbol).strip()
    if not sym:
        return None

    candidates = _mock_candidates(sym, amplitude_pct)
    # ONLY the slippage-adjusted variants. paper_engine always multiplies the
    # quote by (1 ± slippage) before booking a fill, so a raw mock value can never
    # appear as an exit_price of an engine close — while round numbers inside the
    # default 98..102 band (fixtures, placeholder prices like 101.00) hit it
    # constantly. Accepting the raw value bought no detection and produced only
    # false positives. All 12 real incidents carry a slippage factor.
    variants: tuple[tuple[float, float], ...] = (
        (-slippage_pct, 1.0 - slippage_pct),  # sell fill: long close
        (slippage_pct, 1.0 + slippage_pct),  # buy fill: short close
    )
    if include_raw:
        # Zuletzt geprueft: ein Slippage-Treffer ist der staerkere Beleg und soll
        # gewinnen, wenn beide zutraefen.
        variants = (*variants, (0.0, 1.0))
    for applied, factor in variants:
        for raw, phase in candidates.items():
            if raw * factor == value:
                return MockPriceMatch(
                    symbol=sym,
                    price=value,
                    mock_raw_price=raw,
                    phase=phase,
                    slippage_applied=applied,
                    coverage=_curve_coverage(sym, amplitude_pct),
                )
    return None


def is_mock_derived_price(
    symbol: str,
    price: object,
    *,
    slippage_pct: float = DEFAULT_PAPER_SLIPPAGE_FRACTION,
    include_raw: bool = False,
) -> bool:
    """True when ``price`` can only have come from the synthetic mock adapter."""
    return (
        match_mock_price(symbol, price, slippage_pct=slippage_pct, include_raw=include_raw)
        is not None
    )


def coverage_tiers(*, amplitude_pct: float = _DEFAULT_AMPLITUDE_PCT) -> dict[str, list[str]]:
    """Reichweite des Verfahrens, nach Aussagekraft gestaffelt.

    Aus ``_BASE_PRICES`` ABGELEITET, nicht danebengeschrieben: kommt ein Symbol
    im Mock hinzu, waechst die Einstufung automatisch mit. Eine gepflegte
    Zweitliste waere genau die Doppel-Invariante, die schon dreimal
    auseinandergelaufen ist (#723 / #748 / #755).

    ``strong``      Slippage-Treffer zaehlt als belastbare Evidenz.
    ``reportable``  Treffer wird vorgelegt, aber nur als Verdacht.
    ``suppressed``  Muenzwurf — wird gar nicht gemeldet.

    JEDES Symbol ohne eigenen Basispreis laeuft auf den Default und ist damit
    ``suppressed``; die Liste kann das nicht aufzaehlen, weil sie offen ist.
    """
    tiers: dict[str, list[str]] = {"strong": [], "reportable": [], "suppressed": []}
    for sym in sorted(_BASE_PRICES):
        cov = _curve_coverage(sym, amplitude_pct)
        if cov < MAX_STRONG_COVERAGE:
            tiers["strong"].append(sym)
        elif cov < MAX_REPORTABLE_COVERAGE:
            tiers["reportable"].append(sym)
        else:
            tiers["suppressed"].append(sym)
    return tiers


__all__ = [
    "DEFAULT_PAPER_SLIPPAGE_FRACTION",
    "MAX_REPORTABLE_COVERAGE",
    "MAX_STRONG_COVERAGE",
    "MockPriceMatch",
    "coverage_tiers",
    "is_mock_derived_price",
    "match_mock_price",
]
