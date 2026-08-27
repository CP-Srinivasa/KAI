# Evidence Record — DASH/USDT, 2026-05-17

**DOCUMENTATION_ONLY.** Dieses Dokument konserviert eine forensische Beobachtung.
Es ändert weder den Bestand noch eine Klassifikation noch eine Regel.

```
PERFORMANCE_STATE_CHANGED = NO
QUARANTINE_STATE_CHANGED  = NO
POLICY_CHANGED            = NO
REQUIRES_VERIFICATION     = YES
```

`bayes_quarantine.quarantine_reason()` liefert für beide unten beschriebenen
Zeilen unverändert `mock_synthetic_exit_price`. Die hier vorgeschlagene
Umklassifizierung ist **nicht vollzogen** — sie ist eine Operator-Entscheidung
und gehört in einen eigenen Vorgang.

Erhoben am 2026-08-27 aus der Read-Only-Kalibrierungsstudie der Phantom-Kappe
gegen `artifacts/paper_execution_audit.jsonl` auf dem Pi.

---

## 1. Die betroffenen Zeilen

Beide Closes gehören zu **derselben Position**: `entry_price =
40.960469999999994`, `position_side = long`, geschlossen als `tp_tier`-Stufen.

| Feld | Close A | Close B |
|---|---|---|
| `event_type` | `position_partial_closed` | `position_partial_closed` |
| `timestamp_utc` | `2026-05-17T20:43:13.231945+00:00` | `2026-05-17T20:43:13.237064+00:00` |
| `order_id` | `ord_c2cd5f49a17c` | `ord_0c098f3b3c3f` |
| `fill_id` | `fill_845c68b5a4a4` | `fill_69cd25549788` |
| `symbol` | `DASH/USDT` | `DASH/USDT` |
| `entry_price` | `40.960469999999994` | `40.960469999999994` |
| `exit_price` | `101.5492` | `101.5492` |
| `position_side` | `long` | `long` |
| **implied return** | **+147,9200 %** | **+147,9200 %** |
| `reason` | `tp_tier` | `tp_tier` |
| `quantity_closed` | `12.97619078420841` | `12.976190784208406` |
| `remaining_quantity` | `12.976190784208406` | `0.0` |
| `trade_pnl_usd` | `778.3045890937892` | `778.304589093789` |
| `realized_pnl_usd` (kumulativ) | `1831.5867787945706` | `2609.8913678883596` |

Strittige Summe: **1.556,61 USD**.

Der implied return folgt `app/execution/phantom_filter.py::implied_close_return`
— für `long` also `exit / entry - 1`.

## 2. Warum die bestehende Signatur nicht trägt

Der Exit trifft die Kurve des Mock-Adapters bit-exakt:

```
101.6 × (1 − 0,0005) = 101.5492        phase = 212
```

Aber `DASH/USDT` hat **keinen eigenen Eintrag in `_BASE_PRICES`** und läuft damit
auf den Default-Basispreis 100. Dort besetzt die Kurve **199 von 199**
Preisstufen des Bandes `[100,01 … 101,99]` — lückenlos auf dem
2-Nachkommastellen-Raster:

```
coverage       = 100,0 %
reportable     = False
strong_capable = False
```

Die Abdeckung *ist* die Falsch-Positiv-Rate eines Treffers, sofern der echte
Preis im Band liegt. Bei 100 % beweist ein Treffer nichts: jeder auf zwei Stellen
quotierte Preis dieses Bandes trifft ebenfalls.

## 3. Same-Tick-Korroboration: keine

In der Sekunde `2026-05-17T20:43:13` existieren **genau diese zwei** Closes.
Beide sind schwach. Es gibt keinen belastbaren Mock-Close (Symbol mit eigenem
Basispreis, dünne Kurve) im selben Tick, der den Tick-Kontext-Schluss tragen
würde.

Zum Vergleich — bei den drei anderen schwachen Treffern im Bestand trägt er:

| Zeitpunkt | schwacher Treffer | belastbarer Partner in derselben Sekunde |
|---|---|---|
| `2026-07-08T23:22:23` | SOL/USDT | **BTC/USDT** `65798.354365` (coverage 0,3 %) |
| `2026-07-09T02:38:18` | MKR/USDT | **BTC/USDT** `66272.949915` (coverage 0,3 %) |
| `2026-08-12T23:06:34` | SOL/USDT | **ETH/USDT** `3225.6863500000004` (coverage 5,6 %) |
| **`2026-05-17T20:43:13`** | **DASH ×2** | **— keiner —** |

## 4. Korroboration aus derselben Position

Die Position wurde in **drei** `tp_tier`-Stufen geschlossen:

| # | `timestamp_utc` | `order_id` / `fill_id` | `exit_price` | implied | Kurventreffer | quarantäniert |
|---|---|---|---|---|---|---|
| 1 | `2026-05-17T08:00:33.364163+00:00` | `ord_4692d286e611` / `fill_72468b89f97b` | **`42.188895`** | +2,9991 % | **kein** | nein |
| 2 | `2026-05-17T20:43:13.231945+00:00` | `ord_c2cd5f49a17c` / `fill_845c68b5a4a4` | `101.5492` | +147,92 % | schwach | ja |
| 3 | `2026-05-17T20:43:13.237064+00:00` | `ord_0c098f3b3c3f` / `fill_69cd25549788` | `101.5492` | +147,92 % | schwach | ja |

Stufe 1 bepreist DASH bei **42,19**, also auf Entry-Niveau (40,96), trifft die
Kurve **nicht** und ist unauffällig. Zwölf Stunden und 42 Minuten später sollen
dieselben Tier-Exits bei **101,55** liegen — dem 2,4-fachen.

Zwei weitere Auffälligkeiten:

- Stufe 2 und 3 liegen **6 Millisekunden** auseinander und tragen auf acht
  Stellen **denselben** Exit-Preis. Das ist die Signatur *einer* Preisabfrage,
  die für beide Tiers wiederverwendet wurde.
- `101.5492` liegt im Mock-Default-Band, `42.188895` liegt außerhalb.

```
SAME_TICK_CORROBORATION            = NO
SAME_POSITION_INCONSISTENCY        = YES
REPEATED_IDENTICAL_EXIT_WITHIN_6MS = YES
```

Das ist **kein** Beweis für Mock-Erzeugung. Es ist deutlich mehr als „der Return
ist groß", und es senkt den Beweisstandard nicht ab.

## 5. Provenance Status

| Feld | Wert |
|---|---|
| entry provenance | **UNKNOWN** — kein `market_data_source:`-Zyklus über `order_id` im Loop-Audit, kein `document_id` für den Screener-Join |
| exit provenance | **UNKNOWN** — `price_source = None` |
| `price_source` | `None` (Feld existiert erst seit #737, hier nicht gesetzt) |
| venue | **UNKNOWN** — nicht persistiert |
| available evidence | `entry_price`, `exit_price`, `quantity_closed`, `reason=tp_tier`, drei Tier-Stufen derselben Position, Kurventreffer (nicht diskriminierend) |
| missing evidence | Venue-Kerze der Schließungsstunde, `price_source`, `observed_market_price`, `market_data_is_stale`, Tick-Kontext |

**UNKNOWN ist nicht FALSE.** Eine fehlende Provenienz belegt weder, dass der
Preis synthetisch war, noch dass er real war. Der implied return ist eine
zweiseitige Größe `R = f(entry, exit, side)`; hier ist **keine** der beiden
Seiten belegt.

## 6. Klassifikation

```
MOCK_SYNTHETIC_EXIT_PROVEN = NO
OFF_MARKET_CLOSE_PROVEN    = NO
VERIFIED_REAL              = NO

CURRENT_CLASSIFICATION     = REQUIRES_VERIFICATION
CORROBORATING_PATTERN      = SAME_POSITION + IDENTICAL_EXIT_REUSE
```

Begründung: `QUARANTINE` verlangt **positive Korruptionsevidenz**. Der einzige
direkte Beleg — der Kurventreffer — ist bei 100 % Abdeckung ein Münzwurf. Was
vorliegt, ist ein ungelöster Wahrheitszustand mit verstärktem Verdacht.

⚠ **Nicht ins Performance-Buch zurücknehmen.** „Nicht bewiesen korrupt" ist nicht
„bewiesen legitim". Beide Closes bleiben aus den Kennzahlen heraus, bis eine
unabhängige Verifikation vorliegt. Falsch ist allein das Etikett.

Das saubere Dreieck, dem dieser Record folgt:

```
QUARANTINE             positive Korruptionsevidenz
REQUIRES_VERIFICATION  Wahrheit ungelöst / Evidenz unzureichend
VERIFIED               positive Stützevidenz
```

## 7. Die offene Falsifikationsfrage

Dieser Record behauptet **nicht**, dass DASH falsch ist. Er benennt die eine
Messung, die den Fall entscheidet:

```
DASH/USDT
1h candle
2026-05-17T20:00:00Z

QUESTION:  101.5492 ∈ [low, high] ?
```

Genau zwei saubere Ausgänge:

**YES →**
```
MARKET_COMPATIBLE = YES
Mock-Hypothese nicht belegt.
Weitere Provenienzprüfung nötig.
NICHT automatisch VERIFIED_REAL, solange die Exit-Provenienz fehlt.
```

**NO →**
```
off_market_close mechanisch bewiesen.
classification reason = off_market_close
NICHT               = mock_synthetic_exit_price
```

Diese Unterscheidung ist der Kern: ein Preis kann nachweislich nicht
marktkompatibel sein, ohne dass damit bewiesen wäre, **welcher** Mechanismus ihn
erzeugt hat.

Kontrollmessung derselben Art für Stufe 1: Stunde `2026-05-17T08:00:00Z` gegen
`42.188895`. Methodisch identisch zu `app/learning/verified_real_closes.py` —
Roh-Preis aus dem gebuchten Exit zurückrechnen (`exit / (1 − 0,0005)`), gegen
`[low, high]` der Kerze halten.

**Die Kerzen-Verifikation ist bewusst NICHT Teil dieses Records.** Sie erfordert
einen externen Datenabruf und gehört als eigener, reproduzierbarer
Forensikschritt geführt — mit Quelle, Abrufzeitpunkt, Venue und exakten
OHLC-Werten. Fällt sie eindeutig aus, kann dieses Dokument durch einen zweiten
Commit ergänzt werden.

## 8. Verweise

- `app/execution/phantom_filter.py` — `implied_close_return`, kanonische Kappe
- `app/learning/bayes_quarantine.py` — `quarantine_reason`, aktuell `mock_synthetic_exit_price`
- `app/market_data/mock_price_forensics.py` — Kurven-Rekonstruktion; Abdeckung als Falsch-Positiv-Rate
- `app/learning/verified_real_closes.py` — Methodik der Kerzen-Verifikation (CYS/SLX/VELVET)
- `app/execution/close_classification.py` — das vierstufige Urteil
- `docs/audit/phantom_close_artifact_register.md` — Bestand für alle Agenten
