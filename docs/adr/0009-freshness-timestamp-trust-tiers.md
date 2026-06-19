# ADR 0009 — KORREKTUR: Die „RSS-Feed-Latenz" war ein `mktime`/Timezone-Parse-Artefakt

**Status:** Accepted (2026-06-19)
**Stufe:** Edge-Diagnose **Track 3.4** (docs-only, read-only — KEINE Runtime-Änderung in dieser ADR)
**Korrigiert:** den in der Mainline gemergten 0009-Vorinhalt (#361 — artefakt-basierte
Freshness-/Timestamp-Tiers), der auf den falschen +60-min-Latenzzahlen beruhte.
**Bezug ADR 0008:** dessen Latenz-Kernthese („die ~67–94 min sind reale, quellseitige Latenz,
**kein** Timezone-/Parse-Artefakt") war falsch und wurde von der Parallel-Session bereits per
**Revert #360 aus der Mainline entfernt**; diese ADR hält die korrekte Lesart fest.
**Baut auf / bestätigt:** ADR 0007 (Generator NO_EDGE/NO_GO); die 06-18-Recon der Parallel-Session,
die den `mktime`-Artefakt zuerst identifiziert hatte.
**Code-Fix:** bereits in der Mainline via **PR #362** (`rss/adapter.py` `mktime`→`calendar.timegm`,
inkl. TZ-erzwingendem Regressionstest).

## Kontext

Track 3.4 sollte — wie in 0008 vertagt — die Freshness-/Timestamp-Trust-Tiers formal festschreiben.
Beim Zuordnen der Tiers fiel die **Signatur** der „Feed-Latenz" auf: cryptobriefing (~39 % des
Korpus) median **67 min**, **stdev 5,6 min**, 91 % aller Werte im engen Fenster 55–75 min. Echter
quellseitiger Feed-Lag ist variabel (manche Artikel in Minuten sichtbar, andere Stunden später) —
ein so **enges, konstantes Plateau ist die Signatur eines konstanten Offset-Bugs**, nicht von Lag.

## Befund (deterministisch, am Live-Code verifiziert)

`app/ingestion/rss/adapter.py:204` (vor Fix):

```python
published = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=UTC)
```

`feedparser.published_parsed` ist eine **UTC**-`struct_time` mit `tm_isdst=0`. `time.mktime`
interpretiert sie als **Lokalzeit**; weil `tm_isdst=0` Standardzeit erzwingt, ergibt das auf einem
CET/CEST-Host (`Europe/Berlin`) einen **konstanten +1 h-Offset**, ganzjährig (nicht +2 h trotz CEST,
weil `tm_isdst=0` die Standardzeit CET erzwingt).

Deterministische Repro auf dem Pi (`Europe/Berlin`):

```
struct 2026-06-19 10:00:00 UTC →  mktime-Pfad: 09:00:00Z (falsch, −1 h)
                                  timegm-Pfad: 10:00:00Z (korrekt)
Offset (correct − buggy) = +60 min
```

→ `published_at` wurde **1 h vor** der echten Publikationszeit gespeichert, also war
`published_at→fetched_at` **um ~60 min zu groß**.

### Korrigierte Latenz (measured − 60 min)

| Source | measured median | **korrigiert (echt)** |
|--------|----------------:|----------------------:|
| cryptobriefing | 67 min | **~7 min** |
| decrypt | 67 min | **~7 min** |
| coindesk | 68 min | **~8 min** |
| btc_echo | 68 min | **~8 min** |
| beincrypto | 85 min | ~25 min |
| cointelegraph | 98 min | ~38 min |

Die RSS-Hauptquellen sind also **FAST/FRESH (~7–8 min)**, nicht „DELAYED ~67 min". Das „~67 min
Feed-Lag-Plateau" über *alle* RSS-Quellen war durchgehend der konstante +60-min-Parse-Offset.

## Was diese Korrektur invalidiert

1. **ADR 0008, Kernthese:** „Damit ist die Latenz reale Publish→KAI-sichtbar-Latenz, kein
   Timezone-/Parse-Artefakt." → **falsch**; es WAR ein Timezone-Parse-Artefakt (0008 wurde
   deshalb bereits via Revert #360 aus der Mainline entfernt).
2. **Original-ADR-0009 (Vor-Inhalt dieser Datei):** die Tier-Zuordnung „cryptobriefing =
   FIXED_FEED_LAG ~67 m", „keine Quelle erfüllt FAST/FRESH", „~53 % strukturell fix-verspätet" —
   beruhte auf den artefakt-behafteten Zahlen und ist hinfällig.
3. **NO_EDGE ist NICHT latenzbedingt.** Wenn die Hauptquellen ~7–8 min frisch sind, erklärt Latenz
   das NO_EDGE nicht. Die Wurzel liegt damit wieder bei der **Signalqualität** (IC≈0, ADR 0007) —
   konsistent mit der 06-18-Recon.

## Was weiterhin gilt (unabhängig vom Bug)

- **NewsData.io ~12 h** ist **kein** `feedparser`/`mktime`-Pfad → echter Aggregator-Batch, bleibt
  strukturell stale / research_only.
- **Backfill/Garbage-Quellen** (sec_edgar, OKX Announcements, blockworks, sascha_huber_podcast):
  Original-/Filing-Datum bzw. unbrauchbare Timestamps → aus Timing-/Latency-Edge-Metriken
  ausschließen (das `mktime` betrifft nur den RSS-`published_parsed`-Pfad).
- **Daten-Qualitäts-Gaps:** `topics`+`crypto_assets` JSON 0 % befüllt; **kein** `analyzed_at`/
  `created_at` (Analyse-Queue-Latenz DB-seitig nicht messbar); Source-Casing-Splits
  (`Decrypt`/`decrypt`, `CoinTelegraph`/`cointelegraph`, `TheBlock`/`theblock`); `is_duplicate`
  durchgehend 0.
- **Zwei-Dimensionen-Lens** (Frische × Timestamp-Vertrauen) bleibt ein nützliches
  Klassifikationsraster — aber die Quell-Zuordnung MUSS gegen **korrigierte** (post-Fix)
  Timestamps neu erhoben werden, nicht gegen die +60-min-Werte.

## Entscheidung / Konsequenz

1. Der Code-Fix (`mktime`→`calendar.timegm`) ist bereits via **PR #362** in der Mainline; er
   wurde read-only blast-radius-geprüft: **kein sub-hour-Freshness-Gate** liest `published_at`
   (einziges hartes Gate `real_analysis_paper_selector._is_fresh` = 48 h; `_novelty`/
   `_fallback_novelty` = grobe 24 h/7 d-Stufen; Narrative-Velocity/Acceleration = 24 h, advisory).
   Der Live-Decision-Effekt von 60 min ist vernachlässigbar; der Fix korrigiert gespeicherte
   Timestamps und Offline-Messfenster (`offline_baseline.py`).
2. **Bestehende DB-`published_at` bleiben historisch +1 h verschoben.** Reine Latenz-/Timing-
   Auswertungen über den Misch-Zeitraum müssen den Cutoff (Pi-Deploy-Zeitpunkt von PR #362) beachten.
3. Die nächste Edge-Arbeit zielt **nicht** auf Latenz, sondern auf **Signalqualität** (ADR 0007):
   Eventtyp-Magnitude, Asset-Reaktion, Novelty — segmentiert, gegen korrigierte Timestamps.

## Out of Scope (was diese ADR ausdrücklich NICHT tut)

- Keine Quelle wird geblockt, geboostet, downranked.
- Keine Runtime-/Gate-/Execution-/Sizing-/Poll-Änderung **aus dieser ADR** (der Timestamp-Fix ist
  separat in PR #362, bereits gemergt, mit eigener Blast-Radius-Begründung).
- Kein Overclaim „News ist tot": die enge Aussage ist umgekehrt — die RSS-Quellen sind **frischer**
  als 0008/0009-Vorinhalt behauptete; das NO_EDGE liegt an der Signalseite, nicht an der Latenz.

## Follow-ups (read-only, separat zu autorisieren)

1. **Re-Auswertung nach Deploy:** Track-3-Latenz einmalig gegen post-Fix-Timestamps neu erheben
   (Bestätigung ~7–8 min) + Tier-Zuordnung neu festschreiben.
2. **Datenmodell-Gap (`analyzed_at`):** Analyse-Queue-Latenz (`fetched→analysis→candidate→
   resolved`) ist mangels persistierter Stufen-Timestamps nicht messbar — Instrumentierung als
   separater Track.
3. **Track 3.5 — event_type × asset × direction × forward_bps**, segmentiert, gegen **korrigierte**
   Timestamps (nicht +60 min).
4. **Source-Casing-Dedup** in Klassifikation/Metriken normalisieren.
