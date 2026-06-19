# ADR 0008 — Upstream Latency Bottleneck: RSS/Aggregator Feed-Lag verhindert handelbaren News-Edge

**Status:** Accepted (2026-06-19)
**Stufe:** Edge-Diagnose Track 3.2 (read-only, keine Runtime-Änderung)
**Folge-Tracks:** 3.3 (Realtime Source Feasibility), 3.4 (Freshness Tiering)
**Verwandt:** ADR 0007 (Generator NO_EDGE/NO_GO); Track 2.5b (MFE/MAE-Whipsaw)

## Kontext

Track 2 hat den aktuellen Generator-/News-Signalpfad als NO_EDGE/NO_GO belegt (ADR 0007):
robuster EV sub-cost, Precision übersetzt sich nicht in Forward-bps, MFE/MAE ist Whipsaw,
Exit-/TP-/Horizon-Tuning ist nicht gerechtfertigt. Offen blieb die **Ursache**: schwacher
Signaltyp, schlechte Asset-Wahl, geringe Magnitude — **oder zu späte Sicht**.

Track 3.2 (read-only Timestamp-Recon über `canonical_documents`) klärt die Timestamp-Semantik,
die diese Frage bisher blockierte:

- `published_at` = **echte Artikel-Publikationszeit** (aus dem Feed).
- `fetched_at` = **KAI-Poll-Batch-Zeit** (default `now(UTC)` bei Objekt-Erzeugung im Ingestion-Code).
- **Beweis Batch-Charakter:** mehrere Dokumente teilen ein *identisches* `fetched_at`
  (z.B. 3 cryptobriefing-Artikel von 06:07–06:09 alle mit `fetched_at=07:15:58.336793`).
- `is_duplicate=0` durchgehend; Haupt-News-Quellen 0 % `>24h` → keine Repost-Verfälschung.
- Eine Analyse-Stufen-Latenz ist DB-seitig **nicht** messbar (kein persistiertes `analyzed_at`),
  liegt aber downstream und ist klein gegen den ~60min-Feed-Lag.

Damit ist die Latenz **reale Publish→KAI-sichtbar-Latenz**, kein Timezone-/Parse-Artefakt.

## Entscheidender Befund

Die ~67–94 min Latenz der Hauptquellen ist real und **quellseitig**, dominiert von zwei
gestapelten Komponenten — **nicht** von KAI-Analysezeit:

1. **Feed-Lag:** mehrere RSS-Quellen exponieren Artikel erst ~60 min nach `published_at`
   (der 07:15-Poll zog Artikel von 06:07–06:09).
2. **Poll-Kadenz:** Batch-Polling (geteiltes `fetched_at`) addiert bis zum Poll-Intervall.
3. **Aggregator-Delay:** NewsData.io ist mit ~12 h Median strukturell stale.
4. **Backfill-Quellen:** SEC/OKX-Records sind Filing-/Archiv-Ingestion (Tage–Wochen), keine
   Reaktionslatenz.

### Source-Latenzklassifikation (Track 3.2, recent 30d, dedup, `published_at→fetched_at`)

| Source            |     n | median | p95   | Befund / Nutzung |
|-------------------|------:|-------:|------:|------------------|
| cryptobriefing    | 9087  |  67m   |  76m  | echt (Feed-Lag+Poll); Research/Kontext, nicht sub-hour-Trigger |
| decrypt           |  390  |  67m   |  76m  | Research/Kontext, nicht Fast-Trigger |
| cryptoslate       |  208  |  66m   |  76m  | Research/Kontext |
| bitcoin_magazine  |  139  |  66m   |  75m  | Research/Kontext |
| coindesk          |  602  |  68m   |  84m  | hochwertig, aber zu spät für Fast-Reaction |
| btc_echo          |  386  |  68m   |  74m  | Research/Kontext |
| beincrypto        |  846  |  84m   | 103m  | latent; Kontext |
| cointelegraph     |  599  |  94m   | 669m  | stark latent + hohe Streuung; nur mit Vorsicht |
| NewsData.io       | 2149  | 735m   | 750m  | Aggregator ~12 h strukturell stale; nicht tradingfähig |
| YouTube           |  592  | 220m   | 2404m+| gemischt/alte Reposts; nur Research/Kontext |
| sec_edgar         |   27  | ~39 d  | ~74 d | Filing-Backfill; KEINE Reaktionslatenz |
| OKX Announcements |   22  | ~25 d  | ~40 d | Backfill; nicht short-horizon |

Overall (recent 30d, dedup): n≈15 269, Median **70 m**, p75 84, p90 732, p95 743 (p90+ von
NewsData.io/Filings getrieben).

## Entscheidung

KAI versucht **nicht**, den aktuellen RSS-/Aggregator-News-Generator-Pfad über Downstream-Tuning
zu retten — kein Exit-/TP-/Horizon-Sprint, kein Source-Boost, kein Downrank auf Precision-Basis,
kein bearish-Invert, keine Sizing-/Cap-/„a/b"-Änderung.

Das beobachtete NO_EDGE-Verhalten ist **konsistent mit Upstream-Latenz**: die meisten aktuellen
Quellen werden für KAI erst sichtbar, **nachdem** das ökonomisch nutzbare Reaktionsfenster
wahrscheinlich abgeklungen ist. Das deckt sich mit Track 2.5b (MFE/MAE symmetrisch,
MFE_before_MAE ≈ 50 %, Forward-EV sub-cost → Volatilität statt Richtung): der Signalpfad ist nicht
nur schwach, sondern wahrscheinlich **zu spät**.

**Präzisions-Caveat (kein Overclaim):** Latenz ist eine **plausible primäre Ursache** und ein
**hinreichender Grund**, kein Downstream-Harvesting zu optimieren — sie ist **nicht** mathematisch
als *einzige* Ursache bewiesen (schwache Eventtypen, Asset-Wahl, Magnitude können zusätzlich
beitragen). Aber die ~67–94 min Source-Lag disqualifizieren diese Quellen für kurzfristige
Trading-Ambition.

Zukünftige Signalarbeit priorisiert **first-seen-Latenz, realtime/push-nahe Quellen,
Event-Novelty und Eventtyp-Magnitude** — bevor irgendeine Downstream-Trading-Logik geändert wird.

## Konsequenzen

Sofort:
- kein Exit-/TP-/Horizon-Sprint für den alten Generator
- kein Source-Boost auf Hit/Miss-Basis, kein Downrank auf alter Precision allein
- kein bearish-Invert, kein Sizing-/Cap-/„a/b"-Eingriff
- RSS-/Aggregator-News bleiben **Research/Kontext**, nicht primärer short-horizon-Trigger

Positiv:
- Track 3 verschiebt den Fokus upstream (Frische statt Ernte)
- Quellen werden nach Fetch-/first-seen-Latenz klassifiziert
- Eventtypen werden erst tradingrelevant, wenn first-seen früh genug ist

## Out of Scope (was diese ADR ausdrücklich NICHT tut)

- keine Runtime-Gate-Änderung, keine Trading-Freigabe
- keine Source-Blockade, keine Telegram-/Notifier-Änderung
- kein Realtime-Provider-Kaufbeschluss
- **nicht** „alle RSS sind wertlos" / „News ist tot" — die Aussage ist eng:
  *die aktuelle Quellen-/Ingestion-Form ist für kurzfristigen Trading-Edge zu spät.*

## Folge-Tracks (read-only, separat zu autorisieren)

**Track 3.3 — Realtime Source Feasibility:** Exchange-Announcement-APIs, Exchange-Websockets/-Feeds,
offizielle X-Accounts/Firehose-Äquivalent, On-Chain-Event-Streams, Liquidation/Funding-Feeds,
Webhook-Direktquellen, Issuer/Projekt-Feeds, SEC/EDGAR-Realtime wo relevant, RSS mit schnellerem
Poll **nur** wenn der Feed-Lag niedrig ist.

**Track 3.4 — Freshness Tiering (zunächst nur Analyse-Klassifikation, KEIN Runtime-Gate):**
`REALTIME <60s` · `FAST 1–5m` · `DELAYED 5–30m` · `STALE 30–120m` · `ARCHIVAL >120m/backfill`.
Nur `REALTIME/FAST` wären für short-horizon-Signalforschung eligible; `DELAYED/STALE/ARCHIVAL`
bleiben Kontext/Research.
