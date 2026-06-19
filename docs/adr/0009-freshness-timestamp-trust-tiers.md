# ADR 0009 — Freshness & Timestamp-Trust Tiers für News-basierte Signal-Forschung

**Status:** Accepted (2026-06-19)
**Stufe:** Edge-Diagnose **Track 3.4** (docs-only, read-only — KEINE Runtime-Änderung)
**Baut auf:** ADR 0008 (Upstream Latency Bottleneck — deferred Tiering explizit auf Track 3.4),
ADR 0007 (Generator NO_EDGE/NO_GO)
**Datenbasis:** Track 3.2 read-only Recon über `canonical_documents` (n=27.213, 2026-03-19…06-19)

## Kontext

ADR 0008 hat belegt: die Publish→KAI-sichtbar-Latenz ist real und quellseitig (Feed-Lag + Poll +
Aggregator-Batch + Backfill), **nicht** KAI-Analysezeit, und Polling ist **nicht** der Hauptengpass.
0008 hat die formale Tier-Definition bewusst auf Track 3.4 vertagt. Diese ADR schreibt sie fest —
als **Mess- und Interpretationslogik**, damit künftige Eventtyp-/Source-/Signal-Entscheidungen auf
einer kanonischen Frische-/Vertrauens-Klassifikation stehen und dieselben Diskussionen
(„warum nicht schneller pollen / cryptobriefing boosten / NewsData nutzen / alle News handeln")
nicht wiederkehren.

**Diese ADR ändert nichts produktiv** (siehe Out-of-Scope). Sie definiert, WIE Frische und
Timestamp-Vertrauen zu lesen sind.

## Zwei orthogonale Dimensionen

Frische und Timestamp-Vertrauen sind getrennt zu bewerten — eine Quelle kann zuverlässig spät
sein (vertrauenswürdig + DELAYED) oder kaputte Timestamps haben (Frische gar nicht bestimmbar).

### A) Freshness-Tiers (Feed-Lag `published_at→fetched_at`, NUR auf vertrauenswürdigen Timestamps)

| Tier      | Regel (median / p90)        | Nutzung |
|-----------|-----------------------------|---------|
| REALTIME  | median < 60s, p90 < 5m      | short-horizon Signal-Forschung erlaubt |
| FAST      | median ≤ 5m, p90 ≤ 15m      | tradingfähig untersuchbar |
| FRESH     | median ≤ 15m, p90 ≤ 60m     | nur vorsichtige Signal-Forschung |
| DELAYED   | median 15–120m              | Research/Kontext, kein Fast-Trade |
| STALE     | median > 120m (strukturell) | research_only |

### B) Timestamp-Trust-Klassen (orthogonal zur Frische)

| Klasse            | Erkennung | Konsequenz |
|-------------------|-----------|------------|
| TRUSTED           | `published_at` plausibel; Latenz erklärbar | Frische gilt; in Timing-Metriken |
| FIXED_FEED_LAG    | enger fixer Offset (tightness (p90−p10)/median ≲ 0.25), z.B. ~67m | echter Feed-Offset; NICHT als Echtzeit werten |
| AGGREGATOR_BATCH  | konstanter Stunden-Offset (z.B. ~12h, tight) | strukturell stale; research_only |
| BACKFILL_ARCHIVAL | Filing/Announcement, Tage–Wochen, Original-Datum | KEINE Reaktionslatenz; aus Timing-Edge-Metriken AUSSCHLIESSEN |
| GARBAGE_TIMESTAMP | unbrauchbare `published_at` (garbage-rate ≥30%, Median Tage–Jahre) | aus ALLEN Latenz-/Timing-Metriken AUSSCHLIESSEN |
| UNKNOWN           | n<20 oder unklare Semantik | nicht gate-relevant |

## Quell-Zuordnung (Track 3.2, `published_at→fetched_at`)

| Source | n | median | p90 | tightness | Freshness | Timestamp-Trust | Nutzung |
|--------|--:|-------:|----:|----------:|-----------|-----------------|---------|
| cryptobriefing | 10705 | 67m | 74m | 0.19 | DELAYED | **FIXED_FEED_LAG** (~67m, dominiert ~39% Korpus) | Research/Kontext, kein sub-hour-Trigger |
| NewsData.io | 3804 | 735m | 749m | 0.04 | STALE | **AGGREGATOR_BATCH** (~12h fix) | research_only, nicht tradingfähig |
| X/Twitter | 1813 | 33m | 665m | hoch | DELAYED (heavy tail) | TRUSTED/MIXED_TAIL | frischste Quelle, aber nicht sauber FRESH |
| beincrypto | 1808 | 86m | 224m | 1.85 | DELAYED | TRUSTED | Kontext, zuverlässig verspätet |
| coindesk | 1705 | 71m | 718m | 9.33 | DELAYED (heavy tail) | TRUSTED | hochwertig, zu spät für Fast-Reaction |
| cointelegraph | 1836 | 109m | 850m | 7.14 | DELAYED (heavy tail) | TRUSTED | nur mit Vorsicht |
| decrypt | 1149 | 70m | 1112m | 15.0 | DELAYED (heavy tail) | TRUSTED | Research/Kontext |
| btc_echo | 924 | 69m | 552m | 7.10 | DELAYED | TRUSTED | Research/Kontext |
| thedefiant | 593 | 115m | 1500m | 12.5 | DELAYED | TRUSTED (14% garbage) | qualitativ stark, timingseitig zu spät |
| YouTube | 1192 | 360m | 1297m | – | STALE | MIXED/GARBAGE | research_only |
| sec_edgar | 27 | ~39d | – | – | – | **BACKFILL_ARCHIVAL** | Filing-Backfill, keine Reaktionslatenz |
| OKX Announcements | 22 | – | – | – | – | **BACKFILL/GARBAGE** (91%) | nicht short-horizon |
| blockworks / sascha_huber_podcast | 100 / 277 | – | – | – | – | **GARBAGE_TIMESTAMP** (100% / 96%) | aus Timing-Metriken ausschließen |

**Kernbefund:** **Keine Quelle erfüllt FRESH/FAST/REALTIME.** Die frischeste (X/Twitter, 33m
Median) hat p90 ~11h. cryptobriefing (FIXED_FEED_LAG ~67m) + NewsData (AGGREGATOR_BATCH ~12h) =
~53% des Korpus strukturell fix-verspätet. Polling ist nicht der Engpass (Poll-Gaps Minuten,
Batch-Ingestion); der Lag ist quellseitig/`published_at`-semantisch.

## Entscheidung

1. Frische wird **nur** auf TRUSTED/FIXED_FEED_LAG-Quellen aus `published_at→fetched_at` berechnet.
2. GARBAGE_TIMESTAMP, BACKFILL_ARCHIVAL und AGGREGATOR_BATCH werden aus **Timing-/Latency-Edge-
   Metriken ausgeschlossen** (sonst verzerren sie jede Forward-bps-Messung).
3. Für kurzfristige Signal-Forschung sind nur **REALTIME/FAST** eligible; **FRESH** mit Vorsicht;
   **DELAYED/STALE** bleiben Kontext/Research. Da aktuell **keine** Quelle FAST erreicht, ist der
   RSS/Aggregator-Pfad für short-horizon-Trading derzeit nicht eligible (konsistent mit 0007/0008).
4. Die nachfolgende Eventtyp-Messung (Track 3.5) läuft **segmentiert** nach Freshness-Tier ×
   Timestamp-Trust — nie auf dem gemischten Stream.

## Out of Scope (was diese ADR ausdrücklich NICHT tut)

- Keine Quelle wird geblockt, geboostet oder downranked.
- Keine Runtime-/Gate-/Execution-/Sizing-/Cap-/„a-b"-/bearish-Gate-Änderung.
- Keine Poll-Frequenz-Änderung, kein Realtime-Provider-Kaufbeschluss.
- Kein ADR-Overclaim („News ist tot") — Aussage eng: die *aktuelle* Quellen-/Ingestion-Form ist
  für kurzfristigen Trading-Edge zu spät bzw. timestamp-semantisch unzuverlässig.

## Follow-ups (read-only, separat zu autorisieren)

1. **Datenmodell-Gap (analyzed_at):** `canonical_documents` hat **kein** `analyzed_at`/`created_at`
   → die Analyse-Queue-Latenz (`fetched_at→analysis_completed→candidate→resolved`) ist nicht
   messbar. Empfehlung: `canonical_inserted_at`, `analysis_started_at`, `analysis_completed_at`,
   `candidate_created_at`, `candidate_resolved_at` instrumentieren (Migration = separater Track).
2. **Source-Casing-Dedup:** `Decrypt`/`decrypt`, `CoinTelegraph`/`cointelegraph`, `TheBlock`/
   `theblock` sind je zwei Ingestion-Pfade derselben Quelle → in Klassifikation/Metriken
   normalisieren.
3. **is_duplicate ungenutzt:** Flag ist durchgehend 0 trotz `content_hash`; exakte Hash-Dups = 0%,
   aber Near-Reposts unerkannt.
4. **Track 3.3 — Realtime Source Feasibility** (push/websocket/Exchange-Announcement-realtime/X-
   Firehose) als Weg zu einem echten FAST/REALTIME-Tier.
5. **Track 3.5 — event_type × asset × direction × forward_bps**, segmentiert nach Tier:
   `event_type | asset_bucket | freshness_tier | timestamp_trust | n | robEV | MFE | MAE | verdict`.
