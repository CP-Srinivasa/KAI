# Breite-Hebel — zwei Vorschläge (§11) gegen BTC-Monokultur im Paper-Book

_2026-06-17 · read-only Diagnose, Go-Vorlage. Nichts gebaut/deployed. Operator-Sign-off vor jedem Build._

## Befundlage (empirisch, Pi, 48h-Fenster)

- Paper-Book-Durchsatz ist auf **~2 Closes/Tag** gedeckelt, weil nur **~1–2 Positionen gleichzeitig** offen sind (max `checked=2` ganztags); der Monitor arbeitet korrekt, Closes sind saubere Round-Trips.
- Die **einzige aktive Paper-Fill-Route** (`real_analysis` News-Feeder) ist BTC-monokulturell: eligible Pool = **7/48h, davon 6 BTC + 1 UNI**.
- Funnel: 1684 analysierte Docs → `non_directional` 1234 (937 ohne Symbol) → 450 direktional (388 mit Symbol) → **eligible 7**.
- Nicht-BTC-direktionale Docs werden fast nur an **`low_directional_confidence` (61) + `weak_directional_signal` (56)** geblockt — kaum priority/source.
- **Gates sind NICHT gegen nicht-BTC voreingenommen:** `directional_confidence` Median BTC 0.60 = nicht-BTC 0.60; Priorität nicht-BTC sogar höher. BTC passt nur öfter, weil es ~2× mehr direktionales Volumen hat (250 vs 138). ⇒ **Gate-Lockerung verbreitert NICHT — sie lässt mehr BTC durch.**
- Der technische Screener (`technical_screener_feed`) deckt zwar ein 34-Symbol-Universe ab, läuft aber **shadow-only** (kein Execution) → trägt 0 zum Paper-Book bei.

---

## Vorschlag 1 — Nicht-BTC-direktionales News-Volumen erhöhen (Source-Expansion)

### Vorschlag
Den eligible-Pool verbreitern, indem mehr **asset-getaggte, nicht-BTC** Crypto-News durch dieselben (unveränderten) Quality-Gates fließen — nicht durch Gate-Lockerung, sondern durch Quellen, die per-Asset-Coverage liefern. Konkret: Messari (G2, `hasNews`/`hasResearch` + sector/asset-Tags, keyless) als Ingestion-Adapter graduieren und die `cryptobriefing`-Dominanz (72% Ingestion, BTC/General-lastig) relativieren.

### Warum jetzt?
Der Closed-Trade-/Edge-Engpass ist Breite, und Breite ist **nur** über mehr nicht-BTC-Volumen erreichbar (empirisch belegt: Gates sind neutral, Pass-Rate ~2–3% für alle Assets). Source-Intake G1/G2 ist bereits autorisiert und teils live (CoinGecko #304) — Messari ist der nächste Schritt im selben Plan.

### Erwarteter Nutzen
Mehr gleichzeitige nicht-BTC-Positionen → mehr Slots → mehr Closes/Tag → schnellerer, **diversifizierter** Edge-Proof (statt BTC-Monokultur). Sekundär: ehrlichere per-Asset/per-Regime-Attribution.

### Datenquellen / Systeme
Messari API (keyless, GO laut Coverage-Report 06-17: 13 Felder, hasNews/hasResearch/sector/category/rank); bestehende `app/ingestion`-Adapterstruktur; Quality-Gates unverändert (`evaluate_directional_eligibility`).

### Umsetzungsweg (klein, testbar)
1. Messari-Ingestion-Adapter (asset-getaggte News-Items) — `app/ingestion/`, Source-Taxonomie `news_api`.
2. Per-Asset-Tagging in die `tickers`/`crypto_assets`-Pipeline (damit `_symbol_for` greift).
3. Shadow-Messung: nicht-BTC-direktionales Volumen vorher/nachher (eligible-Pool-Verteilung).
4. cryptobriefing-Gewicht prüfen/begrenzen (Feed-Diversifikation), **nicht** abschalten.

### Parallel möglich?
Ja — reine Ingestion-Seite, berührt weder Execution noch Gates noch Caps; läuft neben der Parallel-Session-`.env`-Arbeit.

### Aufwand
Minimal: Messari-Adapter ~0.5 Tag (Coverage-GO existiert). Realistisch: 1–2 Tage inkl. Tagging-Verdrahtung + Tests + Shadow-Vorher/Nachher. Blocker: Tagging-Qualität (no_symbol-Thema); Messari-Rate-Limits.

### Risiken
Niedrig: keine Execution-/Gate-Änderung. Restrisiko: mehr Volumen ≠ garantiert mehr eligible (Pass-Rate niedrig); Messbarkeit vor Skalierung. **Keine Gate-Lockerung** (Doktrin).

### Priorität
**P1** — doktrin-konform, niedriges Risiko, adressiert die Wurzel (Volumen), koppelt an laufende Source-Intake-Graduierung.

---

## Vorschlag 2 — `technical_paper_feeder` (LONG-only) — Spec

### Vorschlag
Einen **neuen, default-off, fail-closed** Feeder bauen, der eligible **LONG** technische Screener-Kandidaten als PAPER-Cycles in den Loop injiziert (Spiegel von `real_analysis_paper_feeder`), getaggt `source=technical_screener`, mit **eigener** Entry-Route + **eigenem** Route-Cap. SHORT bleibt aus.

### Warum jetzt?
Der Screener deckt bereits ein breites, nicht-BTC-lastiges 34-Symbol-Universe ab, läuft aber shadow-only → 0 Beitrag zur Breite. Dies ist der direkteste Weg zu nicht-BTC-Paper-Slots, unabhängig vom News-Funnel.

### Erwarteter Nutzen
Mehr gleichzeitige nicht-BTC-Positionen (alt-Momentum) → mehr Slots/Closes; saubere, source-getaggte Kohorte für eine **eigene** Edge-Messung des technischen Pfads.

### Datenquellen / Systeme
`technical_screener_feed.run_technical_screen` (Rel-Stärke vs BTC, bereits gebaut); `shadow_candidate_ledger` (Kandidaten-Quelle); `run_trading_loop_once(mode=PAPER, analysis_source=…)`; `entry_policy`/`check_route_limits`.

### Umsetzungsweg (klein, testbar)
1. `EntryRoute.TECHNICAL_PAPER` + `ROUTE_SOURCE_PREFIXES[...]=("technical_screener",)` + `DEFAULT_TECHNICAL_ROUTE_LIMITS` (EIGENER Cap, getrennt vom $5000-News-Cap).
2. `TechnicalPaperSettings` (env_prefix `TECHNICAL_PAPER_`): `enabled` (default False), `min_strength`, `paper_route_max_*`, `freshness_max_age_hours`.
3. `app/observability/technical_paper_feeder.py` — Mirror von `real_analysis_paper_feeder`: liest eligible LONG-Kandidaten, **hartes LONG-only** (kein `allow_short`), injiziert PAPER, fail-closed über `resolve_entry_policy(...).verdict(TECHNICAL_PAPER)`.
4. `scripts/technical_paper_feed.py` + systemd-Timer (analog `kai-real-analysis-paper-feed`, default disabled).
5. Tests: LONG-only-Invariante, Cap-Enforcement, fail-closed bei disarmed, source-Tagging, kein SHORT je injiziert.

### Parallel möglich?
Teilweise — Code parallel; **Aktivierung erst nach Build + Tests + Operator-Go + Deploy** (live Paper-Pfad).

### Aufwand
Minimal: ~1 Tag (Mirror eines existierenden Feeders). Realistisch: 2 Tage inkl. neue Route/Settings/Tests. Blocker: God-File-Ratchet (entry_policy/settings), saubere Cap-Trennung, Kohorten-Attribution.

### Risiken
- **Edge dünn:** LONG +17bps@60s / Hit 59%, < ~20bps Kostenhürde → tendenziell ~0/leicht-negativ-EV; rein als **Mess-/Breite-Hebel** zu rechtfertigen, nicht als Profit.
- **Cap-Konkurrenz:** ohne eigenen Cap würde er den $5000-News-Cap auffressen → eigener Cap zwingend.
- **Attributions-Vermischung:** anderes Signal-Klasse als News → strikt source-getaggte Kohorten nötig.
- SHORT bleibt aus (verliert: Hit 33–40%).

### Priorität
**P2** — größerer Build + dünner Edge; nachrangig zu Vorschlag 1 (niedrigeres Risiko, gleiche Breite-Wirkung über Volumen). Sinnvoll, wenn der News-Volumen-Hebel allein nicht reicht.

---

## Empfehlung

Vorschlag 1 zuerst (doktrin-konform, niedriges Risiko, Wurzel = Volumen). Vorschlag 2 als Folge, falls Breite nach Source-Expansion weiter unzureichend ist — und nur als bewusst getaggter Mess-Pfad mit eigenem Cap. Beide brauchen Operator-Go vor Build/Deploy.
