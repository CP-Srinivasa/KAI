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

Oberhalb von 20 % liegen **20 von 617 Closes (3,2 %)** — und jeder einzelne ist ein
bekanntes oder begründet vermutetes Artefakt:

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
| +38,82 % | CYS/USDT | 2026-08-11 | +16,44 |
| +28,19 % | SLX/USDT | 2026-06-27 | +152,04 |
| −21,18 % | VELVET/USDT | 2026-06-29 | −128,96 |

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
5. **Offen ist die Feed-Wurzel.** `3225,68635 = 3227,30 × (1 − 0,0005)`; der
   Roh-Feed lieferte 3227,30 zweimal auf den Cent. Woher der Wert kam, ist **nicht
   belegt** — nicht behaupten. Die Kappe fängt die Wirkung, nicht die Ursache.

## 6. Verweise

- `app/execution/phantom_filter.py` — kanonische Schwelle + Kalibrierungs-Kommentar
- `app/execution/paper_engine.py` — Schreibpfad-Breaker
- `app/learning/bayes_quarantine.py` — Signaturen + vereinheitlichtes Urteil
- `app/execution/portfolio_read.py` — `quarantined_pnl_usd` / `quarantined_closes`
- `tests/unit/test_phantom_threshold_single_source.py` — Drift-Contract + Korpus
- `tests/unit/test_phantom_close_breaker_calibration.py` — Schreibpfad-Kalibrierung
