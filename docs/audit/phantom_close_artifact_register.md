# Phantom-Closes: Artefakt-Register und Wächter-Kette

**Zweck.** Dieses Dokument ist der nachvollziehbare Bestand für jeden Agenten und
jeden Programmierer, der Paper-PnL zitiert, aggregiert oder daraus lernt. Wer eine
Buch-Zahl nennt, muss vorher hier hineingesehen haben.

Stand: 2026-08-18. Gemessen live gegen `artifacts/paper_execution_audit.jsonl` auf
dem Pi (617 Closes).

---

## 1. Warum es dieses Register gibt

Ein einzelner falscher Exit-Preis dreht das Vorzeichen des gesamten Paper-Buchs.
Das ist keine Theorie, sondern zweimal passiert:

| Vorfall | Signatur | Schein-PnL |
|---|---|---|
| MATIC, 2026-05-28 | delistetes BitMEX-Instrument, 0,40875 statt real ~0,088 → +364 % je Zyklus, 9 Closes | **+73.548 USD** |
| ETH, 2026-08-11/12 | *byte-identischer* Exit `3225.6863500000004` an zwei verschiedenen Tagen | **+2.255,58 USD** |

Der ETH-Fall hielt das Buch der Epoche `paper_v2_attested` bei **+396,73 USD**,
obwohl es tatsächlich bei **−1.853,45 USD** stand (n=217, Median −13,96 %,
Trefferquote 67/217). Bereinigt ist **jede** Untergruppe negativ. Er lief sechs
Tage unbemerkt durch Operator-Digest (12.08.: „PnL +1506$"), Daily-Briefing
(13.08.: „Realized: +$1.038,04") und Portfolio-Anzeige.

## 2. Die Wächter-Kette

```
Schreibpfad (verhindert Neubuchung)
  paper_engine.check_stop_take → _implied_close_return → _max_close_return_pct
    → weist ab, lässt die Position OFFEN, schreibt close_price_sanity_rejected
    → Konsument: app/alerts/health_check.py   (vorher: KEINER)

Lesepfad (bereinigt die Vergangenheit)
  portfolio_read.compute_realized_by_asset
    → bayes_quarantine.is_corrupt_close
      → 1. exakte forensische Signaturen  (benennen den Vorfall)
      → 2. phantom_filter.is_phantom_close (generische Kappe, fängt NEUE Fälle)
    → verschiebt nach quarantined_pnl_usd — LÖSCHT NICHTS
```

**Die Schwelle ist kanonisch in `app/execution/phantom_filter.py`.** `paper_engine`
importiert sie. Sie darf nirgends ein zweites Mal definiert werden.

## 3. Die kalibrierte Schwelle: 20 %

Gemessen über alle 617 Closes:

```
Median 1,52 %   p90 4,92 %   p95 7,70 %
größter NICHT verdächtiger Close:  17,16 %
------------------- Lücke -------------------
kleinstes Artefakt:                21,18 %
```

Oberhalb von 20 % liegen **20 von 617 Closes (3,2 %)**. Bei der Kalibrierung galten
alle 20 als Artefakt — **für drei war das falsch** (→ §5c). Der geprüfte Bestand:

| implied return | Symbol | Datum | PnL |
|---|---|---|---|
| +368…+361 % (9×) | MATIC/USDT | 2026-05-28 | +73.548 gesamt |
| +147,92 % (2×) | DASH/USDT | 2026-05-17 | +778,30 je |
| +96,85 % | SOL/USDT | 2026-07-08 | +6.475,82 |
| −92,11 % | MKR/USDT | 2026-07-09 | −3.791,75 |
| **+72,11 %** | **ETH/USDT** | **2026-08-11** | **+1.491,19** |
| **+71,54 %** | **ETH/USDT** | **2026-08-12** | **+764,39** |
| +55,21 % | ETH/USDT | 2026-05-26 | +5.643,29 |
| −50,24 % | SOL/USDT | 2026-08-12 | −585,55 |
| ~~+38,82 %~~ | ~~CYS/USDT~~ | 2026-08-11 | **BELEGT ECHT** (§5c) |
| ~~+28,19 %~~ | ~~SLX/USDT~~ | 2026-06-27 | **BELEGT ECHT** (§5c) |
| ~~−21,18 %~~ | ~~VELVET/USDT~~ | 2026-06-29 | **BELEGT ECHT** (§5c) |

Beachten: die Bereinigung entfernt Schein-**Gewinne** *und* Schein-**Verluste**
(MKR −3.791,75). Sie schönt nicht, sie korrigiert.

## 4. Die zwei Fehler, die hierher geführt haben

**(a) Eine Schwelle gegen n=1 gesetzt.** Die 200 % wurden gegen den MATIC-Fall
(+364 %) gewählt — knapp *unter* diesen einen Wert. Alles darunter passierte
ungeprüft. Der Wächter war da, er war nur zu weit eingestellt. Gegenmaßnahme:
`tests/unit/test_phantom_threshold_single_source.py::test_threshold_is_calibrated_not_guessed`
verlangt, dass die Schwelle im gemessenen Fenster (17,16 % … 21,18 %) liegt.

**(b) Die Zahl stand doppelt im Code.** `phantom_filter` trug im Docstring die
Zusage, die Schwelle „mirrors the engine's MAX_CLOSE_RETURN_PCT". Als #722 den
Motor auf 20 % kalibrierte, blieb die Lese-Seite auf 200 % — also genau der Pfad,
der die Vergangenheit bereinigen soll. Gegenmaßnahme: ein Contract-Test lässt
Schreib- und Lesepfad nicht mehr auseinanderlaufen, auch nicht über
`MAX_CLOSE_RETURN_PCT`.

## 5. Regeln für Agenten und Programmierer

1. **Paper-PnL nie ohne Preis-Plausibilität zitieren.** Vor jeder Buch-Aussage:
   Exit/Entry-Verhältnis gegen das Symbol-Band im Fenster prüfen.
2. **Kein Aggregat ohne Zerlegung** — Untergruppen, leave-one-out, Konzentration.
   Beim ETH-Fall war es die Zerlegung, die den Befund trug: bereinigt war jede
   Untergruppe negativ.
3. **Schwelle nur mit Messung ändern.** Wer `MAX_CLOSE_RETURN_PCT` oder die
   kanonische Konstante anfasst, legt die gemessene Verteilung offen. Der
   Korpus-Test nennt beim Aufweiten den konkreten Vorfall, der wieder durchkäme.
4. **Neue Artefakte hier eintragen** — Zeile in der Tabelle unter §3 und, wenn die
   Signatur exakt ist, in `bayes_quarantine`. Die exakte Signatur behält Vorrang
   vor der generischen Kappe, damit der Befund den Vorfall benennt.
5. **Die Feed-Wurzel ist belegt** (2026-08-18, siehe §5b). Der Roh-Feed lieferte
   3227,30, weil kein echter Venue antwortete und die Kette auf den synthetischen
   Mock-Adapter durchfiel. Die Kappe fängt die Wirkung; die Ursache ist jetzt
   ebenfalls geschlossen.

## 5b. Die Feed-Wurzel: der Mock-Adapter (DS-20260818-MOCK-EXIT)

`MockMarketDataAdapter` ist das **letzte Glied der live-aktiven `fallback`-Kette**
(`APP_MARKET_DATA_PROVIDER=fallback`). Er erzeugt deterministisch

```
round(base + base * (amplitude/100) * sin(phase / 1440 * 2π), 2)
    base(ETH/USDT) = 3200,0   amplitude = 2 %   phase = hash(symbol) % 360
```

und setzt dabei **`is_stale=False`, `freshness_seconds=0.0` bedingungslos**. Auf
einem Tick, an dem *kein* echter Venue auflöste, wählte
`FallbackMarketDataAdapter.get_market_data_point` diesen Punkt
(`chosen = fresh_real or real or fresh or resolved`), der Stale-Guard des
Position-Monitors ließ ihn passieren, und jede Position, deren SL/TP der
erfundene Preis kreuzte, wurde dagegen geschlossen.

**Der Nachweis ist bit-exakt**, nicht plausibel:

```
mock(ETH/USDT, phase 101) = 3227,30  → × (1 − 0,0005) = 3225,6863500000004   ← 08-11/08-12
mock(ETH/USDT, phase 297) = 3261,60  → × (1 − 0,0005) = 3259,9692            ← DS-20260601
```

Beide Werte reproduzieren einschließlich der Float-Artefakte. `hash()` auf Strings
ist **pro Prozess randomisiert** — deshalb war der Preis am 11. und 12.08.
byte-identisch (derselbe Server-Prozess) und beim älteren Vorfall ein anderer.

**Zwei Pfade, ein Monitor-Code, unterschiedliche Immunität:** der Cron ruft
`trading monitor-positions --provider coingecko` (Kette ohne Mock → `None` →
Symbol wird übersprungen; im Log als `no_market_data=6` sichtbar). Der
In-Prozess-`PositionMonitorScheduler` im `kai-server` ruft ohne Provider-Angabe
→ `fallback` → **mit** Mock. Am 11.08. protokollierte der Cron um 23:01/23:11
`no_market_data=6`, während der Scheduler um 23:09:58 gegen 3227,30 schloss.

**Umfang (live gemessen, 617 Closes):** 12 Closes in 7 Ticks, netto
**+8.945,47 USD**. In **jedem** dieser Ticks war *jede* Schließung mock-erzeugt —
0 gemischte Ticks bei 514 Close-Sekunden, die Signatur eines einzigen
Monitor-Durchlaufs auf einer vollständig synthetischen Preis-Karte. Der Mock
erklärt **8 der 11** nicht-MATIC-Einträge aus der Tabelle in §3 und fördert
**4 weitere BTC-Closes** zutage, die *unter* der 20-%-Kappe lagen (+2,8 %,
+6,5 %, −6,5 %, −14,8 %) und deshalb von keiner Größenordnungs-Schwelle je
gefunden werden konnten. MATIC (§3, 9×) bleibt davon unberührt — das war das
delistete BitMEX-Instrument, ein anderer Mechanismus.

**Epoche `paper_v2_attested`** (Basis `trade_pnl_usd`, inkl. partial closes,
Stand 2026-08-18, n=215): gebucht **+771,05 USD**, davon mock-erzeugt
**+1.701,54 USD** (4 Closes, darunter ein Schein-*Verlust* von −585,55),
bereinigt **−930,49 USD**. Das Vorzeichen kippt. Der Vermerk dazu ist kanonisch
in `app/execution/epoch_correction.py` hinterlegt und wird über
`/dashboard/api/*` und die Daily-Strategy-Zeile mitgeführt.

**Gegenmaßnahmen.**
- *Quelle geschlossen*: `FallbackMarketDataAdapter` markiert einen Punkt, der nur
  vom Mock stammt, als `is_stale=True` (`source=…|synthetic_not_tradeable`).
  Entry **und** Monitor überspringen ihn dann; eine Position ohne echte Quote
  bleibt offen und zählt als `no_market_data` — sichtbar, statt zu einem
  erfundenen Preis abgerechnet zu werden.
- *Vergangenheit bereinigt*: `bayes_quarantine` trägt die Klasse als **exakte
  Signatur** `mock_synthetic_exit_price` (nicht als Kappe — die kleinen Fälle
  liegen unter jeder Schwelle). Das Audit bleibt unverändert.
- *Erkennung*: `app/market_data/mock_price_forensics.py` rekonstruiert die Kurve.
  Degenerierte Phasen (Basispreis selbst, Amplituden-Extrema) sind ausgenommen —
  dort fällt der Mock-Wert mit runden, legitim vorkommenden Zahlen zusammen
  (SOL 150,00; LTC 102,00), und Bit-Gleichheit beweist dann nichts.

## 5c. Widerlegt: drei „Artefakte" waren echte Trades (2026-08-18)

CYS, SLX und VELVET standen in §3 als Artefakt. Sie sind es **nicht**. Prüfung:
Slippage aus dem gebuchten `exit_price` herausrechnen und den Roh-Preis gegen die
1h-Kerze der Schließungsstunde halten.

| Close | Roh-Preis | 1h-Kerze der Schließungsstunde | im Band? |
|---|---|---|---|
| CYS 2026-08-11 09:28Z, +38,82 %, 30 h Haltedauer | 1,3904 | low 1,3528 / high 1,4077 | **ja** |
| SLX 2026-06-27 15:16Z, +28,19 %, 27 h Haltedauer | 0,4941 | low 0,477 / high 0,497 | **ja** |
| VELVET 2026-06-29 04:49Z, −21,18 %, 17 h Haltedauer | 1,4021 | low 1,35538 / high 1,95335 | **ja** |

Alle drei sind Micro-Caps mit Übernacht-Haltedauer; eine zweistellige Bewegung ist
dort normal. Der Kontrast zu §5b ist scharf: ein Mock-Preis trägt **zwei**
Dezimalstellen und passt nicht ins Symbol-Band des Fensters, diese drei tragen
**vier** und liegen exakt in der Kerze, in der geschlossen wurde.

**Warum das zählt.** Nach §5b werden alle bekannten Artefakt-Klassen exakt per
Signatur gefangen. Damit fängt der generische 20-%-Cap **allein nur noch diese
drei** — netto 0 Artefakte, 3 Falsch-Positive, −39,52 USD Verzerrung. Ein Wächter,
der ausschließlich Unschuldige greift, ist kein Wächter.

Sie stehen jetzt in `bayes_quarantine.VERIFIED_REAL_CLOSES` und werden
freigesprochen. Der Freispruch überstimmt **nur** den generischen Cap, nie eine
exakte Signatur (Reihenfolge in `corruption_reason`, per Test festgehalten).

**Folge für die Schwelle — Operator-Entscheidung, nicht miterledigt:** die
gemessene Lücke verschiebt sich. Größter *belegt* legitimer Close ist jetzt
+38,82 % (CYS), kleinstes verbleibendes Artefakt −50,24 % (SOL 2026-08-12). Die
20 % liegen damit **unterhalb** des größten legitimen Closes — der Cap fängt
strukturell echte Micro-Cap-Trades mit. Er bleibt trotzdem bei 20 %, weil er die
Verteidigung gegen *noch unbekannte* Klassen ist. Ein Aufweiten auf ~45 % (Mitte
der neuen Lücke) wäre eine bewusste Kalibrierungs-Entscheidung.

**Regel daraus:** Wer einen Close als Artefakt einträgt, hält den Roh-Preis gegen
die Kerze der Schließungsstunde. „Liegt über der Schwelle" ist kein Beleg.

## 6. Verweise

- `app/execution/phantom_filter.py` — kanonische Schwelle + Kalibrierungs-Kommentar
- `app/execution/paper_engine.py` — Schreibpfad-Breaker
- `app/learning/bayes_quarantine.py` — Signaturen + vereinheitlichtes Urteil
- `app/execution/portfolio_read.py` — `quarantined_pnl_usd` / `quarantined_closes`
- `tests/unit/test_phantom_threshold_single_source.py` — Drift-Contract + Korpus
- `tests/unit/test_phantom_close_breaker_calibration.py` — Schreibpfad-Kalibrierung
- `app/market_data/mock_price_forensics.py` — Mock-Kurven-Rekonstruktion (§5b)
- `app/market_data/service.py` — `synthetic_not_tradeable` (Quelle geschlossen)
- `app/execution/epoch_correction.py` — Korrektur-Vermerk der Epoche
- `tests/unit/test_mock_price_forensics.py` — Bit-Nachweis + Falsch-Positiv-Schutz
- `bayes_quarantine.VERIFIED_REAL_CLOSES` — belegte Echt-Trades über dem Cap (§5c)
