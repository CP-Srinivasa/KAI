# ADR 0007 — News-/Autonomous-Generator: NO_EDGE / NO_GO (Signalpfad in aktueller Definition)

**Status:** Accepted (2026-06-19)
**Stufe:** Edge-Diagnose Track 2 (read-only, keine Runtime-Änderung)
**Re-Evaluierung:** nach Track 3 (Signal-Redesign / Event-Quality) oder bei neuer Signaldefinition

## Kontext

Mehrstufige read-only Edge-Analyse (Track 2.1–2.5) der `autonomous_generator`-Kohorte
(`shadow_candidate_resolved.jsonl`, ~584 rows) plus der Alert-resolved-Kohorte
(`alert_outcomes` + `canonical_documents`). Anlass: der tägliche Edge-Report stempelt
`autonomous_generator` als INSUFFICIENT/NO_GO; zu klären war, ob das ein Mess-/Pooling-/
Exit-Artefakt ist oder ein echter Signal-Mangel.

Methodische Leitplanke (durchgehend eingehalten): **read-only, keine Parameter-, Gate-,
Risk-, Sizing-, Cap- oder Execution-Änderung.**

## Befund

- **Outlier-Hygiene (§12-Korrektur):** Mittelwert-basierte EV-Aussagen sind auf diesen Daten
  unbrauchbar — 5 von 584 Zeilen (illiquide Microcaps FTT/SPCXB/BAR, ±~100 %-Moves) dominieren
  jeden Mean; der LONG-Mean flippte allein durch 60 neue Zeilen +31 → −1. Belastbar ist nur
  Median/trimmed auf einem Liquiditäts-Universe (DEFAULT_UNIVERSE; Stablecoins/USDC + Microcaps
  mess-seitig raus).
- **Generator-Forward-Edge = FLAT.** Liquiditätsgefiltert, am robusten Zentrum: Median +0…+6 bps
  @1h, Hit ~52 %, in jeder Richtung/jedem Asset — durchweg unter der ~20-bps-Kostenhürde.
- **Richtungs-Asymmetrie ist richtungs-, nicht quellengetrieben** (Alert-Kohorte, Hit/Miss):
  bullish ~62–95 %, bearish 0–25 % über fast alle Quellen. thedefiant = einziger echter
  bullish-Carrier (Alert 95 %); tradingview_webhook = einziger echter Source-Poison (auch
  bullish ~17 %).
- **Track 2.4 — Precision übersetzt sich NICHT in handelbare bps.** Source-attribuiert (Retro-Join
  `resolved.candidate_id → candidate_ledger.document_id → canonical_documents.source_name`, n=564,
  liquide, Median): selbst thedefiant-long liefert nur **+3 bps Median (sub-cost)**, Hit 58 %.
  Keine bullish-Source-Zelle schlägt 20 bps bei ausreichend n. cryptobriefing dominiert die
  Kohorte (330/564) und ist flat.
- **Track 2.5b — kein rettbares Exit-Problem, sondern Whipsaw.** MFE-Median +32 / MAE-Median −28;
  **MFE_before_MAE ≈ 47–51 % (Münzwurf)**, TP@+20 vor Gegenmove nur 21–25 %, Eigen-Geometrie
  own_take 2–3 % vs own_stop 19–22 %. Die Signale markieren **volatile Momente, nicht Richtung.**

## Entscheidung

Der **aktuelle autonomous-/news-Generator-Signalpfad** wird als **NO_EDGE / NO_GO** geführt:

- Erlaubte Nutzung: **measurement / research only** (Shadow).
- NICHT erlaubt (alle durch die Diagnose ausgeschlossen, nicht nur ungetestet):
  Source-Boost, Source-Downrank, bearish-Invert, Runtime-Gate-Tuning,
  Exit-/Take-Profit-/Horizon-Sprint, Sizing-/Cap-/„a/b"-Drehen, Durchsatz-Erzwingen,
  Telegram-Priority→Trades.

Wichtige Abgrenzung: **kein** „Generator für immer wertlos" — der Signalpfad **in seiner
aktuellen Definition** ist nach Kosten nicht handelbar. Downstream-Tuning rettet ihn nachweislich
nicht (2.4 + 2.5b).

## Konsequenzen

- Kein Aufwand mehr ins „besser ernten" dieses Pfads. Der nächste echte Hebel ist **upstream**:
  **Track 3 — Signal-Redesign / Event-Quality** (Event-Taxonomie, Event-Magnitude >20 bps netto,
  Event-Timing/Latenz, Asset-Fit, Source-Fit nach Forward-bps statt Hitrate, Markt-Regime,
  Novelty, Actionability).
- Mess-Infrastruktur dafür: PR #355 (document_id-Durchreichung + `normalize_source_name`,
  measure-only) macht künftige resolved rows source-self-contained; die Brücke ist bereits
  retroaktiv schließbar.
- Schützt vor Wiederholung derselben Diskussion: die NO_GO-Begründung ist hier fixiert
  (sub-cost forward EV + Whipsaw-MFE/MAE-Geometrie).

Verwandt: ADR 0001 (TradingView/Quality-Bar-Kontext); Memory `kai_edge_track2_no_robust_edge_20260618`.
