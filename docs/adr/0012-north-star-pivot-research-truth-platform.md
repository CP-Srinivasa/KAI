# ADR 0012 — NORTH_STAR-Pivot: von Alpha-Jagd zu Research-/Truth-Plattform (Hybrid)

- **Status:** ACCEPTED — Operator-Entscheid 2026-06-29: **Hybrid (Research-Kern, Revenue-Gate)**
- **Datum:** 2026-06-29
- **Betroffen:** NORTH_STAR, PHASE_MAP, DELIVERY_BACKLOG; präzisiert ADR 0007 (generator-path-no-edge)

## Kontext (alles 2026-06-29 verifiziert)

Die zugängliche Signal-Landschaft ist systematisch durchgekämmt und **konsistent widerlegt**:

| Familie | Befund | n |
|---|---|---|
| canonical generator (naive TA), net | P 56,5 % nur Ein-Trade-Artefakt (ohne best: 25 %), Median −89 bps | 62 |
| canonical generator, **gross (pre-cost)** | −4,4 bps, `cost_reachable=false` (Signal vor Kosten negativ) | (06-26, #462) |
| Edge-Discovery-Runner (TA) | 0/12 Survivors BTC/ETH/SOL/BNB | 12 Regeln |
| V5 funding/OI shadow | trust=0,5 SHADOW_ONLY, kein cost-clearing, konzentriert | 749/734 |
| Whale-Transfer | 0 Survivors | (#482) |
| Unlock-Short | beta-neutral TERMINAL widerlegt (pooled −111 bps) | (#487) |
| **Momentum-Universe (heute)** | **signaled-dir net neg ALLE Horizonte (1h −10 … 24h −94)** | **178** |

**Doktrin** (web-grounded, `docs/research/edge_discovery_strategy_20260625.md`): naive Preis-/TA-Signalsuche ist statistisch chancenlos; nach ~100 Trials erzeugt eine NUTZLOSE Strategie erwartete Max-Sharpe ≈ 2,5 aus reinem Selektionsglück; **jeder ungetrackte Trial hebt die Schein-Sharpe-Baseline**. Weitersuchen produziert erwartungsgemäß False Positives.

**Was nachweislich funktioniert** ist NICHT Alpha, sondern die **Falsifikations-/Truth-Infrastruktur**: canonical-edge (epochen-/quellen-sauber), Cost-Truth-Panel (bytes-verifiziert), BH-FDR-Gate-Maschinerie, 14-Punkte-Validierungs-Gate (DSR/PBO/MinTRL/Harvey-Liu), Chain-Truth/OTS, Source-Lifecycle, Counterfactual-Drift. KAIs differenzierte, bewiesene Kompetenz = **rigorose, kosten-ehrliche, auditierbare Widerlegung von Markt-Signal-Hypothesen.**

**Ehrlicher Vorbehalt:** Die naheliegende Monetarisierungs-Säule ist ebenfalls **ungetestet** — die G0-`/oracle/fee-series`-Demand-Probe steht bei **0 Challenges / 0 Payments / 0 Fingerprints (NO-PASS)**. Es gibt weder bewiesenen Alpha NOCH bewiesene zahlbare Nachfrage. Der Pivot ist daher kein „sicherer Hafen", sondern eine **Neudefinition, was KAI beweisen will.**

## Entscheidung

**NORTH_STAR (neu):** KAI ist eine **Research-/Truth-Plattform für auditierbare Markt-Signal-Evaluation und -Falsifikation** — sie erzeugt vertrauenswürdige, kosten-ehrliche, nachvollziehbare Urteile darüber, ob ein Signal handelbaren Edge hat, und macht den Forschungs-/Widerlegungs-Prozess selbst zum Kernwert.

**KAI ist NICHT** (mehr) ein Alpha-generierender Trading-Bot, der Edge aus retail-zugänglichen Daten verspricht.

**Trading** bleibt Paper + gegated; Live-Trading bleibt ein *möglicher künftiger Modul*, AKTIVIERBAR NUR falls je ein Signal das 14-Punkte-Gate überlebt — aber es ist nicht länger der NORTH_STAR.

### Operator-Entscheid: HYBRID — Research-Kern, Revenue-Gate

- **Jetzt:** Research-/Truth-Plattform ist der aktive Kern. Bestmögliche, auditierbare, kosten-ehrliche Falsifikations-/Truth-Infra ist das Ziel. Deckt sich mit der bestehenden Paper-Lern-Direktive.
- **Monetarisierung = späteres Gate, NICHT jetzt:** KEIN aktiver Go-to-Market / Traffic-Push auf /oracle heute. Die Demand-Probe bleibt passiv scharf; Monetarisierung wird erst verfolgt, wenn Nachfrage **organisch** auftaucht (Demand-Probe schlägt von selbst an: ≥3 Payments / ≥2 FP / ≥2 Tage, ADR 0011).
- **PHASE_MAP-Wirkung:** Phase-Fokus = „Truth-Infra härten + Forschungs-/Widerlegungs-Qualität" statt „Shadow-/Live-Readiness". Live-Trading-Phase de-priorisiert (nur reaktivierbar bei Gate-Survivor). Monetarisierungs-Phase als gegateter Folgeschritt geparkt.

## Konsequenzen

- ✅ Bereits getan: Momentum-Feeder aus (`MOMENTUM_UNIVERSE_FEED_ENABLED=false`, reversibel, mit Backup), G0–G7 falsifiziert markiert.
- **Stopp:** keine neuen naiven Edge-Generatoren/Feeder bauen.
- **Härten + produktisieren:** die Falsifikations-/Truth-Infra wird das Produkt (Edge-Truth-Panel, churn/cost-report, edge-validation-CLI, Source-Lifecycle als auditierbarer Dienst).
- **Edge-Forschung** läuft NUR noch diszipliniert, prä-registriert, gegated (max. EINE Wette zur Zeit, z. B. Stablecoin-Flow) — als Seitenkanal, nicht als Mission.
- **Reversal-Hinweis** (Momentum short@24h +55 bps, P 0,913 — verfehlt das 0,95-Tor) als eigene prä-registrierte Hypothese geparkt, NICHT actionable.
- **Werkzeug:** `scripts/falsify_momentum.py` (Offline-Forward-OHLCV-Resolver, kosten-netto, Moving-Block-Bootstrap) belegt das Pattern „measure-first-Shadow direkt falsifizieren statt Monate auf Fills warten".

## Alternativen erwogen

- **Weiter Alpha jagen (verworfen):** widerspricht der eigenen Doktrin (Baseline-Heben); 6+ Familien null.
- **Sofort aufgeben (verworfen):** die teure Falsifikations-Apparatur macht weitere prä-registrierte Tests billig; Wegwerfen verschenkt diese Investition.
- **Reines Revenue-Produkt jetzt (verworfen):** Nachfrage ist 0 und unbewiesen; aktiver Go-to-Market wäre verfrüht. Daher Hybrid mit Revenue als Folge-Gate.
- **Status quo (verworfen):** „insufficient bis n≥30" auf Programmen, deren n nie wächst, ist Stillstand.
