# Kat.-D-Kontrollquellen — Shortlist (V5, Daily 07-10)

**Status: DOKUMENTIERT, Umsetzung bewusst geparkt bis C1-Ende (~2026-08-03).**
Kontaminationsverbot: Während des C1-Demand-Fensters (`9cab81fae4823482`) werden
keine neuen Quellen onboardet und keine Oracle-/Preis-/Listing-Pfade angefasst.

## Kontext

Quellen-Discovery züchtete Signal-Quellen in eine strukturell geschlossene
Graduation hinein (ADR-0012: kein Edge als Graduations-Ziel; Verdikte terminal —
canonical-edge NO_GO, news_direction terminal_dead, funding/oi NOT_MET).
Daily 07-04-V6 → 07-07-V5 → 07-10-V5 haben deshalb entschieden:

1. **Seed-Freeze:** `SOURCE_SCOUT_ENABLED=false` + `SOURCE_DISCOVERY_ENABLED=false`
   (Pi-.env, 2026-07-11). Discovery-Budget hört auf, nicht-graduierbare
   Signal-Quellen zu züchten. Reversibel; Re-Arm nur mit neuem, prä-registriertem
   Graduations-Ziel.
2. **Digest-Relabel:** „N nahe Graduation" (Vanity) → „Graduation strukturell
   geschlossen — ADR-0012"; Zustands-Label „eingefroren — Seed-Freeze".
3. **Kat.-D-Fokus (dieses Dokument):** Die einzigen Quellen, die dem
   Wahrheits-Kern jetzt noch dienen, sind **Kontroll-/Cross-Check-Quellen**
   (unabhängige Referenzdaten zur Verifikation eigener Messungen) — nicht
   weitere Signal-Feeds.

## Shortlist (aus Exploration-Coverage-Probes, Report 2026-07-10, 18 Probes: 9 GO / 9 NO-GO)

| Kandidat | Probe-Verdikt | Records/Fields | Latenz (Median) | Kontroll-Nutzen |
|---|---|---|---|---|
| `dune:api` | GO (auch `dune:snapshot` GO) | 10 / 3 | 233 ms | Onchain-Aggregate als unabhängiger Cross-Check eigener Onchain-/Volumen-Behauptungen |
| `messari:api` | GO (auch `messari:snapshot` GO) | 50 / 13 | 931 ms | Asset-Fundamentaldaten/Metriken als Referenz gegen eigene Markt-Daten-Pfade |
| `coingecko:api` | GO (auch `coingecko:snapshot` GO, 29 Fields) | 8 / 13 | 453 ms | Preis-/Marktdaten-Zweitquelle (Preis-Cross-Check 0704-V3, geparkt bis C1-Ende) |

Nachrücker (GO, geringere Feldtiefe): `coinglass:scrape`, `coinmarketcap:scrape`,
`coinmarketcap:snapshot`. NO-GO u.a.: `glassnode:*`, `nansen:*`, `coinglass:api`,
`coinmarketcap:api` (kein nutzbarer Lauf ohne Key/Anti-Bot).

Quelle: `artifacts/exploration/coverage_report.{json,md}` (Pi, generiert 2026-07-10).

## Umsetzung nach C1-Ende (nicht vorher)

1. 0704-V3 Preis-Oracle-Cross-Check entparken; `coingecko:api` als Referenzpfad.
2. `dune:api`/`messari:api` als read-only Kontroll-Adapter (fail-closed, kein Key
   im Repo) — Zweck: Attestierbarkeit eigener Zahlen, NICHT Signal-Generierung.
3. Re-Arm der Discovery nur mit neuem prä-registriertem Graduations-Ziel.
