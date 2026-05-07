# Lynch Token-Categories — KAI-Adaption Spec (C-Track)

**Operator-Auftrag:** C-Lynch aus dem Cutover-Strategie-Track 2026-05-07.
**Quelle:** Peter Lynch, *Der Boerse einen Schritt voraus* (Original *One Up On Wall Street*, 1989), Kapitel "The Six Categories" (Slow Growers, Stalwarts, Fast Growers, Cyclicals, Turnarounds, Asset Plays).
**Status:** **Spec/Konzept**, keine Implementation. Implementation ist eigener Sprint nach Operator-Sign-off auf den Spec-Inhalt unten.

---

## Warum Lynch's Categories fuer KAI?

KAI liefert heute eine **globale** Forward-Precision-Zahl (`forward_precision_pct = 88.89%`) und eine **globale** Hit-Rate (`precision_pct = 85.15%`). Operator vergleicht die gegen ein Single-Target (Ziel ≥60%). Das ist **strukturell unscharf**:

- Eine 85% Hit-Rate fuer **BTC/ETH-Stalwarts** ist gut, aber nicht spektakulaer (Stalwarts bewegen sich definiert in 30-50%-Range, viele Tagessignale verfehlen nicht weil die Strategie falsch war, sondern weil die Asset-Klasse weniger volatil ist).
- Eine 85% Hit-Rate fuer **Fast-Grower-Memes** waere aussergewoehnlich gut (Memes haben hohe Fail-Quote bei Entry-Timing-Fehlern; +50%/-30% Outcome-Spread erlaubt erst ab ~50% Hit-Rate Ueberleben).
- Eine 85% Hit-Rate fuer **Cyclicals** ist normal, weil Cyclicals durch Sektor-Rotation getrieben sind (DeFi-Q1, NFT-Phasen, Altcoin-Saison) — die Kunst ist Phasen-Erkennung, nicht Stock-Picking.

→ Eine **Kategorie-spezifische Forward-Precision** macht Erfolgsmessung erst aussagekraeftig.

Das ist auch der Punkt, an dem das aktuelle Priority-Tier-Lift-Tile (heute -4.48pp) Sinn ergibt: das System klassifiziert vermutlich High-Conviction nach Sentiment-/News-Volumen, nicht nach Asset-Klasse-Anpassung. Ein Fast-Grower-Hit "+50%" ist wertvoller als ein Stalwart-Hit "+10%" — aktuelle Tier-Logik unterscheidet das nicht.

---

## Die sechs Kategorien — Crypto-Adaption

### 1. Slow Grower (Stalwart-Crossover)

**Lynch Original:** Etablierte, grosse Unternehmen, 2-4% Wachstum/Jahr, Dividend-Payer. Niedrige PE.

**Crypto-Mapping:**
- Mcap > $50bn
- 1y annualisierte Volatilitaet < 40%
- Auf-/Abwaerts-Range typisch 10-30%/Jahr
- Hohe Nutzungs-Reife (BTC = digitale-Gold-Narrative)

**KAI-Tag:** `slow_grower`
**Asset-Beispiele:** BTC (kann zwischen Slow-Grower und Stalwart oszillieren je nach Markt-Phase)
**Forward-Precision-Target:** ≥75% (geringe Volatilitaet → mehr Hit-Rate erwartbar)
**Outcome-Range fuer "Hit":** +5% bis +20% in 30 Tagen
**Operator-Hinweis:** Slow-Grower-Signale niedrig priorisieren wenn Markt aggressiv → Opportunity-Cost vs. Fast-Grower hoch.

### 2. Stalwart (Solide Established)

**Lynch Original:** Reife Branchenfuehrer, 10-12% Wachstum/Jahr, mittlere PE. "30-50% in 1-2 Jahren ist gut."

**Crypto-Mapping:**
- Mcap $5-50bn
- 1y annualisierte Volatilitaet 40-70%
- Etablierte Use-Case + Developer-Activity
- Network-Effects oder First-Mover-Lead

**KAI-Tag:** `stalwart`
**Asset-Beispiele:** ETH (Smart Contract Reference), SOL (High-Performance L1), BNB (CEX-Backbone), XRP (Cross-Border Payment)
**Forward-Precision-Target:** ≥65%
**Outcome-Range fuer "Hit":** +15% bis +50% in 30-90 Tagen
**Operator-Hinweis:** Sweet-Spot fuer KAI's aktuelle Architektur — gute Balance aus Liquiditaet, Coverage und Predictability.

### 3. Fast Grower

**Lynch Original:** Kleine, aggressive Unternehmen, 20-25%+ Wachstum/Jahr, hohe PE. "Diese sind die zehnfachen Bagger."

**Crypto-Mapping:**
- Mcap < $5bn
- 1y annualisierte Volatilitaet > 70%
- Schnelles TVL-/User-Wachstum **oder** virales Narrativ
- Hohe Fail-Quote auf Asset-Klassen-Ebene

**KAI-Tag:** `fast_grower`
**Asset-Beispiele:** AI-Tokens (TAO, RNDR, FET), L2-Native (ARB, OP, MNT), Meme-Tokens mit Substanz (PEPE, DOGE-bei-bestimmten-Phasen)
**Forward-Precision-Target:** ≥45% (struktureller Fail-Rate-Floor)
**Outcome-Range fuer "Hit":** +50% bis +500% in 14-90 Tagen
**Operator-Hinweis:** Position-Size **strikt klein** halten (Lynch: "Investiere niemals mehr als du bereit bist zu verlieren"). Stop-Loss disziplin **kritisch** — Fast-Grower drehen schnell.

### 4. Cyclical

**Lynch Original:** Branchen mit Boom-Bust-Zyklen (Auto, Stahl, Airline). Earnings-Volatilitaet hoch. Timing-Game.

**Crypto-Mapping:**
- Sektor-Rotation-getrieben (DeFi-Sommer, NFT-Welle, Memecoin-Saison, Halving-Cycle)
- 1y Volatilitaet stark zeit-abhaengig (200% in Bull-Phase, 30% in Bear)
- Korrelation > 0.7 zu BTC in Bull, > 0.9 in Crash

**KAI-Tag:** `cyclical`
**Asset-Beispiele:** DeFi-Bluechips (UNI, AAVE, MKR), Mid-Cap-L1s in Saisonal-Phasen (AVAX, NEAR), NFT-Tokens (MANA, SAND)
**Forward-Precision-Target:** Phasen-abhaengig:
- Bullphase aktiv: ≥60%
- Bearphase aktiv: ≥70% bei Short-Bias-Signals, ≥40% bei Long-Bias
- Konsolidierung: ≥50%
**Outcome-Range fuer "Hit":** +30% bis +200% in Bull-Phase, -30% bis -70% Outperformance vs Index in Bear-Phase
**Operator-Hinweis:** Cyclical-Signale **niemals isoliert** bewerten — Markt-Regime-Kontext (BTC-Trend, FGI, DeFi-TVL-Delta) ist entscheidend.

### 5. Turnaround

**Lynch Original:** Unternehmen am Rand des Bankrotts, die sich stabilisieren oder erholen. "Manchmal kommt die Rakete."

**Crypto-Mapping:**
- Drawdown >70% from ATH **und** noch active development
- Re-Launch nach Exploit/Crisis (LUNA → LUNC, FTT-Trauma-Tokens)
- Pivot in neue Use-Case-Domaene
- Token-Restructuring oder Token-Migration

**KAI-Tag:** `turnaround`
**Asset-Beispiele:** Restartete L1s, Recover-Stories (LUNC, BLUR-pre-Surge), Forks
**Forward-Precision-Target:** ≥40% (sehr binaere Outcomes)
**Outcome-Range fuer "Hit":** +100% bis +1000% (Recovery-Multiplier) **oder** -90% bis -100% (zweite Welle)
**Operator-Hinweis:** **Hoechste Risiko-Klasse**. Position-Size <2% Portfolio. Stop-Loss eng. KAI sollte hier eine separate Approval-Wand bauen (zweimaliger Operator-Click).

### 6. Asset Play

**Lynch Original:** Unternehmen mit Vermoegens-Posten, die der Markt unterbewertet (Real Estate, Patente, Cash). "Du bekommst die Firma fuer weniger als die Summe ihrer Teile."

**Crypto-Mapping:**
- Mcap < TVL **oder** Mcap < FDV/2
- Token-Backing durch Treasury (Cash + Diversifizierung)
- Underused L1 mit hochwertigem Tech-Stack
- DAO-Treasuries die deutlich unter Token-Mcap liegen

**KAI-Tag:** `asset_play`
**Asset-Beispiele:** Underused-L1s (TON pre-2024-Surge, Tezos pre-DeFi-Wave), DAO-Tokens mit grossen Treasuries, Tokenized-Real-World-Assets
**Forward-Precision-Target:** ≥55% (Mean-Reversion + Re-Discovery driven)
**Outcome-Range fuer "Hit":** +30% bis +200% in 60-180 Tagen
**Operator-Hinweis:** **Geduldsspiel** — Asset-Plays brauchen Trigger (Marketing-Push, Partnership, Listing). Forward-Window > 90d.

---

## Daten-Quellen fuer Klassifizierung

Die Klassifizierung muss **automatisierbar** und **periodisch re-evaluierbar** sein (Tokens wechseln Kategorien — ETH kann von Stalwart zu Slow-Grower driften wenn Volatilitaet permanent sinkt).

| Daten-Punkt | Quelle | Update-Frequenz | KAI-Status |
|---|---|---|---|
| Mcap | CoinGecko (aktiv) | hourly | OK |
| 1y annualisierte Volatilitaet | berechnet aus CoinGecko-Price-History | daily | TODO |
| TVL | DefiLlama (Vorschlag B-3 Pos. 5) | daily | OPEN |
| FDV | CoinGecko `fully_diluted_valuation` | hourly | OK |
| Sektor-Tags | CoinGecko `categories[]` | weekly | OK |
| ATH-Drawdown | berechnet aus Price-History | daily | TODO |
| 1y BTC-Korrelation | berechnet | weekly | TODO |
| DAO-Treasury | DeFi-Llama Treasuries-Endpoint | weekly | OPEN |

→ **Klassifikator-Implementation kann erst nach DefiLlama-Integration (B-3 Pos. 5) starten.** Die Treasury- und TVL-Daten sind nur dort verfuegbar.

---

## Klassifizierungs-Algorithmus (Pseudocode)

```python
def classify_token(token: Token, market_phase: MarketPhase) -> AssetClass:
    """Klassifiziert ein Token in eine der 6 Lynch-Kategorien.

    Reihenfolge der Pruefungen ist wichtig:
    Asset-Play und Turnaround haben Vorrang vor Mcap-basierten Buckets,
    weil sie strukturelle Eigenschaften beschreiben.
    """
    # 1. Asset-Play (strukturell, Mcap < TVL oder Mcap < FDV/2)
    if token.tvl and token.mcap < token.tvl:
        return AssetClass.ASSET_PLAY
    if token.mcap < token.fdv / 2:
        return AssetClass.ASSET_PLAY

    # 2. Turnaround (Drawdown >70% + active dev)
    if token.drawdown_from_ath_pct > 70 and token.has_active_dev:
        return AssetClass.TURNAROUND

    # 3. Cyclical (Sektor-Rotation-Korrelation)
    if token.is_cyclical_sector and token.btc_correlation_1y > 0.7:
        return AssetClass.CYCLICAL

    # 4. Mcap-basierte Buckets
    if token.mcap > 50_000_000_000 and token.volatility_1y < 0.40:
        return AssetClass.SLOW_GROWER
    if 5_000_000_000 < token.mcap <= 50_000_000_000 and token.volatility_1y < 0.70:
        return AssetClass.STALWART
    if token.mcap < 5_000_000_000 or token.volatility_1y > 0.70:
        return AssetClass.FAST_GROWER

    # Fallback (sollte selten triggern)
    return AssetClass.STALWART
```

---

## Frontend-Integration (Vorschlag)

**Per-Klasse Tile-Sektion** im Dashboard:

```
Forward-Precision per Asset-Klasse
┌─────────────────────┬───────┬──────────┬──────┐
│ Klasse              │ Hit % │ Target   │ n    │
├─────────────────────┼───────┼──────────┼──────┤
│ Slow Grower         │ 82%   │ ≥75%   ✓ │ 23   │
│ Stalwart            │ 71%   │ ≥65%   ✓ │ 41   │
│ Fast Grower         │ 38%   │ ≥45%   ⚠ │ 17   │
│ Cyclical (Bull)     │ 64%   │ ≥60%   ✓ │ 12   │
│ Turnaround          │ —     │ ≥40%     │ 2    │
│ Asset Play          │ —     │ ≥55%     │ 0    │
└─────────────────────┴───────┴──────────┴──────┘
```

Damit sieht Operator direkt: "Fast Grower performen unter Target, da muss ich bei Position-Size oder Stop-Loss-Disziplin nachschaerfen". Die Globalzahl 85% wuerde das verstecken.

---

## Implementierungsreihenfolge (eigener Sprint)

1. **DefiLlama-Adapter** integrieren (B-3 Pos. 5, Vorbedingung).
2. **Module `app/analysis/asset_classification.py`** mit `classify_token()` + Caching (Cache-TTL 24h).
3. **Migration** `alembic/versions/0008_add_asset_class_to_directional_alerts.py` mit neuer Spalte `asset_class TEXT NULL`.
4. **Backfill-Skript** fuer historische `directional_alerts` (~7000 Records, einmaliger Run).
5. **Quality-Endpoint** erweitern um `forward_precision_by_class`.
6. **Frontend-Tile** wie oben skizziert.

**Aufwand-Schaetzung:** 1-2 Tage saubere Arbeit, plus 1-2 Tage Forward-Replay zur Validierung der Klassen-spezifischen Hit-Rates.

---

## Was Lynch *nicht* abdeckt — Folge-Themen

- **Stablecoins** sind keine Lynch-Kategorie. KAI behandelt sie heute schon getrennt (USDT, USDC fallen aus den Signal-Pipes raus).
- **Memecoins ohne Substanz** sind technisch Fast-Growers, aber die Win-Rate ist <30% — Lynch wuerde "Avoid"-Empfehlung geben. Operator-Entscheidung: Komplett-Filter oder Sub-Kategorie?
- **L2-Tokens**: oft Cross-Kategorie (Stalwart + Cyclical). Adapter-Logik muss Mehrfach-Klassifikation erlauben (Hauptklasse + Sekundaer-Tag).

---

## Cross-Refs zu Buchempfehlungen Operator

- **Graham (Intelligent Investieren)** — Margin-of-Safety-Konzept gehoert in Position-Sizing, nicht in Klassifikation. Eigener Spec spaeter.
- **Cholidis (Technische Analyse)** — Multi-Timeframe-Confluence + Volume-Profile sind Signal-Generator-Erweiterungen, orthogonal zu Lynch-Categories. Eigener Spec spaeter.

---

## Operator-Sign-off-Checkliste vor Implementation

- [ ] Sind die 6 Kategorie-Definitionen mit Crypto-Asset-Beispielen treffend?
- [ ] Stimmen die Forward-Precision-Targets pro Klasse aus deiner Sicht?
- [ ] Ist die Reihenfolge der Pruefungen im Klassifikator-Pseudocode richtig (Asset-Play vor Mcap)?
- [ ] Soll Memecoins ohne Substanz Sub-Filter oder eigene Klasse werden?
- [ ] Soll der Klassifikator periodisch re-evaluieren (24h-TTL ok) oder pro Alert frisch?
- [ ] Welche Kategorie hat heute hoechste Operator-Prio fuer den ersten Backfill (Stalwart als Sweet-Spot? Fast-Grower wegen hoher Fail-Quote?)
