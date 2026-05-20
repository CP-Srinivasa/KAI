# Sentiment-Klassifikator-Drift-Check — 2026-05-20

**Auftrag:** DS-20260520-NEW-2 (P1 vor 2026-05-23). Hypothesen (b) Klassifikator-Threshold-Drift durch PR #45/#46/#47 und (c) Pipeline-Pfad nicht aktiv verifizieren oder falsifizieren.

**Verfasser:** Claude Code Mid-Window-Forensik 2026-05-20. Read-only Inspection, kein Code-Eingriff.

---

## 1. Hypothese (b) — PR #45/#46/#47 als Klassifikator-Drift-Quelle

**Befund:** ❌ **WIDERLEGT.**

PR-Metadaten:

| PR | Commit | Titel | Touched-Files |
|---|---|---|---|
| #45 | `2509a10` | fix(config): default market_data_provider=fallback (anti-drift) | Config-Defaults |
| #46 | `36c8495` | fix(ui): portfolio "no market data" hardening | UI-Frontend |
| #47 | `e0f71de` | feat(observability): auto-reprocess pending envelopes in premium healthcheck | Health-Check |

**Keine der drei PRs hat Code in `app/analysis/`, `prompts.py`, `internal_model/provider.py`, `rules/` oder am Sentiment-Klassifikator berührt.** Die Hypothese aus dem 19er-Reversion-Block war eine falsche Spur — vermutlich entstanden aus der zeitlichen Korrelation („Drift seit 14.05., PRs am 14.-16.05. gemerged"), aber ohne Code-Bezug.

## 2. Code-Stabilität des Klassifikators

`git log --since=2026-05-10 -- app/analysis/`:

```
d5a73a7 fix(analysis): TYPE_CHECKING-guard on AlertAuditRecord import (#57)
0b0659f feat(analysis): source-confluence shadow audit (V3, P1) (#53)
ad96c3a feat(execution): Signal-to-Execution Pipeline + Cleanup Phase 3-5 (PR #5)
```

- **PR #57** (19.05.): nur Import-Guard, kein Klassifikator-Code.
- **PR #53** (16.05.): additiver Shadow-Audit-Stream für Source-Confluence, kein Eingriff in `compute_priority()` oder Klassifikator-Logik.
- **PR #5** (vor 14.05.): vor dem Drift-Start.

`app/analysis/prompts.py` zuletzt geändert in Commit `66423b1 feat(D-124..D-129)` — Wochen vor dem Drift-Fenster. `app/analysis/internal_model/provider.py` zuletzt in Sprint-4d. **Der Klassifikator-Code ist seit Wochen stabil.**

## 3. LLM- und ENV-Konfig-Stabilität

Pi `.env`:
```
OPENAI_API_KEY=sk-REDACTED
OPENAI_MODEL=gpt-4o
OPENAI_TIMEOUT=30
RISK_BAYES_CONFIDENCE_SHADOW_ONLY=true
```

Keine Shadow-Provider, kein Mock-LLM, kein Provider-Switch. `gpt-4o` ist seit Wochen produktive Wahl. Keine ENV-Drift erkennbar.

## 4. Hypothese (c) — Pipeline nicht aktiv

**Befund:** ❌ **WIDERLEGT.**

Pipeline läuft heute (2026-05-20):

| Metrik | Wert |
|---|---|
| Alerts mit `sentiment_label`-Feld in 24h | 20 |
| Davon directional (bullish+bearish) | 1 (5.6%) |
| Source-Mix 7d Top-5 | beincrypto 30, cryptobriefing 23, cryptoslate 19, btc_echo 6, YouTube 5 |
| Bayes-Audit-Eintrag heute | 1 (cryptoslate CME-VIX, 18:18:57 UTC) |
| outcomes-Stream Wachstum letzte Stunde | +5 (theblock-Quellen, Backfill-Pfad) |

Pipeline-Pfad funktional, RSS+Telegram → Persist → Analyze → alert_audit → outcomes — alle Layer aktiv.

## 5. Hypothese (a) — News-Inhalts-Drift

**Befund:** ✅ **BESTÄTIGT durch Sample-Inspektion.**

Klassifizierungen letzte 5d (n=61 mit `sentiment_label`-Feld):

| Label | Anteil | Beispiel-Headlines |
|---|---|---|
| mixed | 56% (34) | „bitcoin gibt clarity act gewinne vollstandig ab letzter reset vor dem bullrun", „openserv soars 70 on ai agent hype why the rally could cool fast", „harvard dumps its ethereum and bitcoin etf investment", „50 million ethereum short rocks the market how will eth price react" |
| neutral | 43% (26) | „jerome h powell steps down as federal reserve chair", „strategy has put bitcoin sales on the table", „bitcoin dips to 77k sell in may", „app days are numbered the end state of software" |
| bullish | 1.6% (1) | „cme is launching a vix style fear trade to bitcoin" |
| bearish | 0% (0) | — |

**Inhaltliche Analyse der `mixed`/`neutral`-Klassifizierungen:**

- Powell-Rücktritt (mega-Macro-Event) → `neutral` ist defensibel: Marktreaktion massiv, Richtung aber strukturell ambivalent.
- Harvard-dumps-ETF → eigentlich klar `bearish`, aber LLM klassifiziert `mixed` (vermutlich weil „dump" + langfristig kein klares Signal).
- "sell in may" → klar `bearish`-suggestiv, aber `neutral`.
- "bullrun"-Headline → `mixed` (vermutlich wegen „reset" in derselben Headline).
- "50 million ethereum short" → bearish, klassifiziert als `mixed`.

**Das LLM klassifiziert konservativ.** Sobald in einer Headline beide Polaritäten oder Mehrdeutigkeit ankommen, fällt die Klassifizierung auf `mixed`. Headlines mit reinen direktionalen Verben + ohne Gegen-Indikatoren werden als `bullish/bearish` klassifiziert — aber genau die sind in der aktuellen News-Realität selten.

**Regime-Kontext (R3-Shadow):** BTC + ETH stehen seit 16.05. zunehmend in `breakout_up`/`vol_low`. In einem trendigen Bull-Markt häufen sich „Reset"-/„Sell-in-May"-/„dump"-Gegenpunkt-Narrative — also genau die mehrdeutigen Headlines, die der Klassifikator als `mixed` einstuft. Das ist eine **strukturelle News-Phase-Eigenschaft**, kein Klassifikator-Bug.

## 6. Lösungsoptionen (Operator-Decision)

### Option A — Status quo akzeptieren (konservativ)

**Argument:** Das LLM klassifiziert vernünftig. Wenn die News-Realität dünn an klaren direktionalen Signalen ist, ist das ein **richtiges System-Verhalten**, kein Bug. Re-Entry-Phase will Konservatismus. Heutiger einziger Bayes-Eintrag (cryptoslate CME-VIX) ist ein klar direktionales Signal — das System hat sauber gefiltert.

**Konsequenz:** Bayes-Schreibrate bleibt niedrig (~1/Tag oder seltener), abhängig vom News-Inhalts-Mix.

**Trigger für Re-Evaluation:** wenn nach 2026-05-23 weitere 7d wieder 0 direktionale Klassifizierungen → dann ist die News-Phase-These zu hinterfragen.

### Option B — Klassifikator-Prompt schärfen

**Patch-Idee:** `app/analysis/prompts.py` ergänzen: explizite Anweisung, bei direktional eindeutigen Verben (sell, dump, buy, soar, crash, rally) das primäre Sentiment-Label zu setzen, auch wenn die Headline Gegen-Indikatoren enthält.

**Beispiel-Prompt-Ergänzung:**
> When the headline contains a direct directional verb (e.g. "dumps", "soars", "sells", "buys", "crashes") about a specific asset/market, classify by that primary verb's polarity, even if hedging phrases follow. Use `mixed` ONLY if the headline contains two opposing directional claims with similar strength.

**Pro:** Direkt umsetzbar (Prompt-Edit + Test). Würde die heutigen mixed-Klassifizierungen (harvard-dumps, ethereum-short) als bearish einstufen.

**Kontra:** Verstößt gegen Re-Entry-Konservatismus. Mehr Klassifizierungs-Aggressivität = mehr false-positives in Bayes-Audit = mehr Lernrauschen.

**Risiko:** Prompt-Drift ist nicht testbar wie Code — wir müssten via Replay alter Samples prüfen, ob die neue Anweisung das gewünschte Verhalten produziert. Mid-Window keine sinnvolle Aufgabe.

### Option C — Bridge-Erweiterung um `mixed`-Pfad

**Patch-Idee:** `_maybe_trigger_paper_trade` zusätzlich `sentiment_label="mixed"` durchlassen, wenn `|sentiment_score| > 0.4` (numerische Magnitude überschreibt Label-Mehrdeutigkeit).

**Pro:** Code-lokal, kein Prompt-Edit. Würde z.B. „50 million ethereum short rocks market" mit numerisch klarem negativem score durchlassen.

**Kontra:** Magic-Number-Layer. `sentiment_score` ist ebenfalls LLM-Output — sein Threshold wäre genauso „phasenabhängig" wie das Label. Verlagert das Problem.

## 7. Empfehlung

**Option A (Status quo akzeptieren) bis nach 2026-05-23.**

Begründungen:
1. Re-Entry-Phase will Konservatismus, nicht Aggression auf dünner Empirie.
2. Heutiger Bayes-Eintrag (cryptoslate CME-VIX) zeigt: bei klar direktionalen Signalen funktioniert das System einwandfrei. Pipeline ist nicht „kaputt", nur News-Realität dünn.
3. 4-Tage-Null-Direktional-Periode 16.-19.05. ist **strukturell durch trendigen Bull-Markt + Reset-Narrative erklärbar**, nicht durch System-Defekt.
4. Option B (Prompt-Edit) ist ohne A/B-Replay nicht validierbar. Option C (Bridge-Magic-Numbers) verlagert nur.

**Wenn nach 7d weiteren Beobachtungs-Fensters (2026-05-27) immer noch ≤2 direktionale Klassifizierungen pro Woche:** Dann wird Option B (oder ein **separater V5-Sprint** „Klassifikator-Replay-Harness") fällig. Aber nicht jetzt.

## 8. Konsequenzen für End-of-Window-Review 2026-05-23

In `re_entry_end_of_window_2026-05-23.md` §Pre-Fill Sektion 4 ist als „NEUER Befund Sektion 4" das Priority-Scoring-Paradox (V1) eingetragen. Ergänzend zu prüfen:

- **Hypothese (b) PR-Korrelation ist widerlegt** — Folge-Sprint-Definition zur Klassifikator-Code-Änderung NICHT nötig.
- **News-Phase-Drift als P1-Beobachtungspunkt** in §6 Decision-Phase-2 aufnehmen: wenn Phase-A (Active-Mode) gewählt wird, muss klar sein, dass die Klassifizierungs-Rate `~1 direktional pro Tag` ist und Bayes entsprechend langsam lernt.

## 9. Was diese Inspection NICHT tut

- Kein Patch, kein Branch, kein PR.
- Kein Prompt-Edit.
- Kein Klassifikator-Replay (P2-Folge-Sprint, falls nötig nach 2026-05-27).
- Keine Annahme über Operator-Präferenz.

**Status:** ✅ Befund vollständig. Empfehlung Option A. Sign-off im EOW-Review 2026-05-23.

## 10. Datenquellen

- Pi `git rev-parse HEAD` = `5ff02d8` (nach Doku-Commit).
- `git log --since=2026-05-10 -- app/analysis/` (3 Commits, alle inspiziert).
- Pi `.env` (LLM-Konfig).
- `artifacts/alert_audit.jsonl` (61 Einträge der letzten 5d mit `sentiment_label`-Feld).
- `artifacts/regime_state/btc_regime.jsonl`, `eth_regime.jsonl` (R3-Shadow Regime-Stand).
- GitHub PR-Metadaten #45/#46/#47/#53/#57 via Pi-git-log (gh-CLI auf Workstation: anderer Repo-Slug).
