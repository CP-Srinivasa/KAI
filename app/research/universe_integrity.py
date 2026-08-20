"""Universe Integrity Preflight — was genau wird eigentlich versiegelt?

Vor T0 einer Praeregistrierung muss das Universum ein **Artefakt** sein, kein
Verweis. Zwei Gruende, beide am 2026-08-20 konkret aufgetreten:

**1. Ein Ticker kann tot sein, ohne dass es auffaellt.** Die Haeufigkeitsmessung
ergab fuer ``MATIC/USDT`` **0 Feuerungen** bei 4.301 auswertbaren Kerzen. Das
sind zwei voellig verschiedene Aussagen — "die Regel feuerte dort nie" oder "der
Ticker liefert nur noch Legacy-Daten" — und ohne Aufloesung waere die falsche
davon still in ein versiegeltes Universum gewandert. Polygon hat MATIC auf POL
migriert; welcher Binance-Pair-Name heute handelbar ist, wird hier **live aus
exchangeInfo** bestimmt, nicht aus Erinnerung.

**2. Eine dynamische Referenz ist kein Universum.**
``technical_screener_feed.DEFAULT_UNIVERSE`` kann sich waehrend eines
90-Tage-Fensters aendern. Versiegelt wird deshalb eine ausgeschriebene Liste
kanonischer Namen plus ``UNIVERSE_SHA256`` — danach gilt fuer das ganze
OOS-Fenster:

    neu gelistet         -> NICHT aufnehmen
    ploetzlich spannend  -> NICHT aufnehmen
    schlechte Performance-> NICHT entfernen
    delistet             -> definierter DATA_UNAVAILABLE, KEINE Substitution

Dieses Modul trennt strikt: die Bewertung ist rein und in CI pruefbar, die
Beschaffung (exchangeInfo, Backfill) ist injiziert. Es sieht **keine Renditen**
— das ist Voraussetzung dafuer, dass der Preflight vor T0 laufen darf.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

# Versionsstempel im Hash. Aendert sich die Serialisierung, aendert sich der
# Hash — ein alter Seal laesst sich dann nicht stillschweigend neu erzeugen.
UNIVERSE_SPEC_VERSION = "kai/universe/v1"

# Erwartete Handelsstatus-Kennung des Providers.
STATUS_TRADING = "TRADING"

# Deklarierte Legacy-Umbenennungen. Diese Tabelle sagt nur, WO nachzusehen ist;
# ob das Ziel wirklich handelbar ist, entscheidet ausschliesslich der Live-Check.
# Sie ist bewusst klein und explizit — eine heuristische Alias-Erkennung waere
# genau die Sorte Magie, die man in einem versiegelten Artefakt nicht will.
# Jeder Eintrag ist am 2026-08-20 gegen exchangeInfo geprueft worden: die linke
# Seite steht dort auf BREAK, die rechte auf TRADING. Alle drei sind reine
# Umbenennungen/Redenominierungen — fuer eine renditebasierte Regel ist das
# irrelevant, weil Renditen skaleninvariant sind.
LEGACY_RENAMES: Mapping[str, str] = {
    # Polygon: MATIC -> POL (Migration auf Polygon PoS, 1:1).
    "MATIC/USDT": "POL/USDT",
    # Render Network: RNDR -> RENDER (1:1).
    "RNDR/USDT": "RENDER/USDT",
    # Maker -> Sky: MKR -> SKY (Redenominierung 1:24.000; Preisniveau anders,
    # Renditen identisch).
    "MKR/USDT": "SKY/USDT",
}

# Befunde, die das Versiegeln blockieren.
BLOCKING_ISSUES: frozenset[str] = frozenset(
    {
        "not_listed",
        "not_trading",
        "rename_target_not_trading",
        "duplicate_after_canonicalisation",
        "alias_collision",
        "insufficient_history",
        "volume_unusable",
    }
)


@dataclass(frozen=True)
class ProviderSymbol:
    """Ein Handelspaar, wie der Provider es meldet."""

    pair: str  # "BTCUSDT"
    status: str  # "TRADING", "BREAK", "HALT", ...
    base_asset: str
    quote_asset: str


@dataclass(frozen=True)
class DataFacts:
    """Was der Backfill fuer ein Symbol tatsaechlich geliefert hat.

    Bewusst NUR Verfuegbarkeit und Vollstaendigkeit — keine Preise, keine
    Renditen. Was hier nicht steht, kann den Preflight auch nicht verunreinigen.
    """

    bars: int = 0
    gap_bars: int = 0
    positive_volume_bars: int = 0


@dataclass(frozen=True)
class SymbolIntegrity:
    """Das Urteil ueber ein einzelnes Symbol."""

    research_symbol: str
    canonical_symbol: str
    provider_pair: str | None
    status: str | None
    facts: DataFacts
    issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(issue in BLOCKING_ISSUES for issue in self.issues)


@dataclass(frozen=True)
class UniverseIntegrityReport:
    """Das Artefakt, das versiegelt wird (bzw. der Grund, warum noch nicht)."""

    spec_version: str
    symbols: tuple[SymbolIntegrity, ...]
    canonical_universe: tuple[str, ...]
    universe_sha256: str
    blocking: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.blocking


def to_provider_pair(research_symbol: str) -> str:
    """``"BTC/USDT"`` -> ``"BTCUSDT"``. Trennerlos und gross, sonst unveraendert."""
    candidate = research_symbol.strip().upper()
    for separator in ("/", "-", ":"):
        if separator in candidate:
            base, _, quote = candidate.partition(separator)
            return f"{base}{quote}"
    return candidate


def universe_sha256(canonical_symbols: Sequence[str]) -> str:
    """Reihenfolge-unabhaengiger Hash der kanonischen Namen.

    Sortiert, damit eine Umsortierung der Quellliste denselben Seal ergibt —
    und mit Versionspraefix, damit eine geaenderte Serialisierung sichtbar wird
    statt still denselben Hash zu produzieren.
    """
    body = "\n".join(sorted(canonical_symbols))
    payload = f"{UNIVERSE_SPEC_VERSION}\n{body}\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_universe(
    research_symbols: Sequence[str],
    provider_symbols: Mapping[str, ProviderSymbol],
    data_facts: Mapping[str, DataFacts] | None = None,
    *,
    renames: Mapping[str, str] = LEGACY_RENAMES,
    min_bars: int = 0,
) -> UniverseIntegrityReport:
    """Kanonisiere und pruefe ein Universum. Rein: keine Netz-, keine Dateizugriffe.

    Args:
        research_symbols: die deklarierte Liste (KAI-Schreibweise, ``BASE/QUOTE``).
        provider_symbols: Pair -> Providerangabe, aus exchangeInfo.
        data_facts: kanonisches Symbol -> was der Backfill lieferte. Fehlt es,
            werden nur Listing/Status/Duplikate geprueft.
        renames: deklarierte Legacy-Umbenennungen (Absicht, nicht Beweis).
        min_bars: geforderte Mindestzahl Kerzen; 0 schaltet die Pruefung ab.

    Returns:
        Ein Report mit einem Urteil je Symbol, der kanonischen Liste und ihrem
        Hash. ``ok`` ist False, sobald irgendein blockierender Befund vorliegt —
        der Hash wird trotzdem berechnet, damit man sieht, WORUEBER gestritten
        wird.
    """
    facts_by_symbol = dict(data_facts or {})
    declared = list(research_symbols)
    declared_set = set(declared)

    results: list[SymbolIntegrity] = []
    canonical_seen: dict[str, str] = {}  # kanonisch -> erstes Research-Symbol

    for symbol in declared:
        issues: list[str] = []
        canonical = symbol
        pair = to_provider_pair(symbol)
        entry = provider_symbols.get(pair)

        if entry is None or entry.status != STATUS_TRADING:
            target = renames.get(symbol)
            if target is not None:
                # Eine deklarierte Umbenennung: das Ziel muss handelbar sein,
                # sonst ist die Tabelle veraltet und das faellt hier auf.
                target_pair = to_provider_pair(target)
                target_entry = provider_symbols.get(target_pair)
                if target_entry is not None and target_entry.status == STATUS_TRADING:
                    if target in declared_set:
                        # Sonst entstuende still eine 33er-Familie mit einem
                        # doppelt gewichteten Asset.
                        issues.append("alias_collision")
                    canonical = target
                    pair = target_pair
                    entry = target_entry
                    issues.append("renamed")
                else:
                    issues.append("rename_target_not_trading")
            if entry is None:
                issues.append("not_listed")
            elif entry.status != STATUS_TRADING:
                issues.append("not_trading")

        previous = canonical_seen.get(canonical)
        if previous is not None:
            issues.append("duplicate_after_canonicalisation")
        else:
            canonical_seen[canonical] = symbol

        facts = facts_by_symbol.get(canonical, DataFacts())
        if data_facts is not None:
            if min_bars > 0 and facts.bars < min_bars:
                issues.append("insufficient_history")
            if facts.bars > 0 and facts.positive_volume_bars == 0:
                issues.append("volume_unusable")

        results.append(
            SymbolIntegrity(
                research_symbol=symbol,
                canonical_symbol=canonical,
                provider_pair=pair if entry is not None else None,
                status=entry.status if entry is not None else None,
                facts=facts,
                issues=tuple(issues),
            )
        )

    canonical_universe = tuple(dict.fromkeys(r.canonical_symbol for r in results))
    blocking = tuple(
        f"{r.research_symbol}: {issue}"
        for r in results
        for issue in r.issues
        if issue in BLOCKING_ISSUES
    )
    return UniverseIntegrityReport(
        spec_version=UNIVERSE_SPEC_VERSION,
        symbols=tuple(results),
        canonical_universe=canonical_universe,
        universe_sha256=universe_sha256(canonical_universe),
        blocking=blocking,
    )


def report_to_dict(report: UniverseIntegrityReport) -> dict[str, object]:
    """Serialisierbare Form fuer das Praeregistrierungs-Artefakt."""
    return {
        "spec_version": report.spec_version,
        "universe_sha256": report.universe_sha256,
        "n_symbols": len(report.canonical_universe),
        "canonical_universe": list(report.canonical_universe),
        "ok": report.ok,
        "blocking": list(report.blocking),
        "symbols": [
            {
                "research_symbol": s.research_symbol,
                "canonical_symbol": s.canonical_symbol,
                "provider_pair": s.provider_pair,
                "status": s.status,
                "bars": s.facts.bars,
                "gap_bars": s.facts.gap_bars,
                "positive_volume_bars": s.facts.positive_volume_bars,
                "issues": list(s.issues),
            }
            for s in report.symbols
        ],
    }


def report_to_json(report: UniverseIntegrityReport) -> str:
    return json.dumps(report_to_dict(report), indent=2, sort_keys=True, ensure_ascii=False)
