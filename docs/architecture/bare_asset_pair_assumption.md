# Bare-Asset Pair Assumption

Stand: 2026-05-14. Rekonstruiert nach Drift 2026-05-14 12:20.
Referenz: DECISION_LOG.md -> ### D-223.

## Annahme

Wenn ein Alert-Audit-Record ein nacktes Crypto-Asset in `affected_assets`
enthaelt, z. B. `BTC` statt `BTC/USDT`, bewertet die Outcome-Annotation dieses
Asset implizit gegen das USDT-Paar.

Diese Annahme gilt fuer hit/miss-Annotation und Preis-Checks, nicht fuer Live-
Execution-Freigaben.

## Wo sie greift

- `app/alerts/auto_annotator.py::_primary_symbol`
  - `BTC` wird zu `BTC/USDT`.
  - Bereits slash-formatierte Assets bleiben unveraendert.

- `app/alerts/price_check.py::check_alert_price_moves`
  - `BTC` wird vor CoinGecko-Fetches zu `BTC/USDT`.
  - `PriceCheckResult.asset` bleibt `BTC`, das Fetch-Symbol ist `BTC/USDT`.

- `app/alerts/tv_bridge.py`
  - Bare-TV-Ticker wie `BTC` koennen als `affected_assets=["BTC"]` in den
    Alert-Audit-Pfad gelangen, wenn der Base-Asset in CoinGecko bekannt ist.
  - Der Bridge-Fix reicht keinen leeren Quote downstream weiter; die
    USDT-Annahme entsteht erst in Annotation/Price-Check.

## Warum das aktuell akzeptabel ist

Die Re-Entry-Auswertung misst directionale Crypto-Alerts gegen eine einheitliche
USD-nahe Referenz. Fuer die heute relevanten BTC/ETH/major-crypto Alerts ist
`<ASSET>/USDT` die vorhandene Default-Paarannahme in CoinGecko- und
Annotation-Pfaden.

## Wann sie bricht

- Wenn ein Signal absichtlich ein anderes Quote-Asset meint, z. B. `BTC/USDC`,
  `ETH/BTC` oder ein perp-spezifisches Venue-Symbol.
- Wenn `affected_assets` nicht nur Asset-Identitaet, sondern ein konkretes
  handelbares Paar ausdruecken soll.
- Wenn spaetere TV- oder Operator-Signale quote-sensitive Performance messen
  sollen.

## Guardrail

Pinning-Tests ergaenzt am 2026-05-14:

- `tests/unit/test_auto_annotator.py::test_primary_symbol_pins_bare_asset_to_usdt_pair`
- `tests/unit/test_alert_price_check.py::test_check_alert_price_moves_pins_bare_asset_to_usdt_pair`

Wenn diese Annahme spaeter geaendert wird, sollen die Tests bewusst angepasst
werden und die Entscheidung gehoert in `DECISION_LOG.md`.
