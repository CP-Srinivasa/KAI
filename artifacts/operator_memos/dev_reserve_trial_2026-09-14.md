# Entwicklerreserve — drei Kontrollaufgaben und Einstufung (2026-09-14)

Gehört zu ADR 0020 / D-CORE-009, Runbook `docs/runbooks/dev_reserve_opencode.md` §4.
Client: OpenCode 1.18.25 (Laptop) → SSH-Tunnel → LiteLLM-Dev-Proxy 127.0.0.1:4001
(Release `5972835e`, Transport 1.99.0) → `kai-dev-*`. Alle Sitzungen in frischen
Worktrees ohne `.env`. Token je Sitzung aus der OpenCode-Datenbank
(`~/.local/share/opencode/opencode.db`), Preise nach Anbieterliste (Stand 14.09.).

## Vorlauf

- Anbieter-Schlüssel wurden beim verdeckten Einfügen (`read -rs`) **verdoppelt**
  (70/102 statt 35/51 Zeichen). Beide Anbieter antworteten 401. Halbiert, Proxy neu
  gestartet, Smoke danach PASS. Lehre: nach verdeckter Eingabe die Länge prüfen.
- Smoke (`dev_reserve.sh smoke`): economy 2,07e-05 USD, code 4,53e-05 USD je Probe;
  LiteLLM kennt beide Preise. Frontier nicht geprobt.

## Ergebnisse

| # | Aufgabe | Route / Modell | Schritte · Dauer | Token (in / out / reasoning / cache-read) | Kosten (Liste) | Ergebnis | Regelverstöße |
|---|---|---|---|---|---|---|---|
| 1 | Repository nur analysieren (Dokumentfluss Ingestion → Alert, Budgetstelle) | `kai-dev-economy` · DeepSeek V4.1 Flash | 12 · 2 min 21 s | 81.257 gesamt | ≈ 0,01–0,03 USD | Alle Dateien/Funktionen existieren, **alle Zeilenzahlen exakt**, Budgetstelle korrekt. Zwei Unschärfen: `decide_pot` steht nicht in `gateway.py`; Sendeversuche landen in `alert_delivery_audit.jsonl`. | keine, `git status` leer |
| 2 | Isolierten Fehler beheben + Tests (Mojibake `≤` in `pipeline.py:97`) | `kai-dev-code` · Kimi K2.7 Code | 3 · 1 min 36 s | 78.045 (76.544 Cache) | ≈ 0,02 USD | Genau eine Datei, eine Zeile, Bytes korrekt (E2 89 A4); pytest 5 passed, ruff sauber, Diff gezeigt, kein Commit. Gemergt als **#971 `8416412b`**. | keine |
| 3 | Fremden Diff prüfen (#970 / D-272, `5972835e..e25eab08`, 8 Dateien) | `kai-dev-code` · Kimi K2.7 Code | 26 · 7 min | 68.725 / 1.612 / 20.761 / 978.176 | ≈ 0,34 USD | 6 Funde, alle nur lesend. F1 korrekt (Anbietername-Fallback paart modellunabhängig, abgemildert durch Token/ok/120 s/1:1). **F2 korrekt und wertvoll**: kein Datums-Cutoff, neue Innenzeile ohne Außenzeile kann fremd gepaart werden; PR-Text nennt einen „abgeschlossenen Zeitraum“, der Code kennt keinen. F3 korrekt (Zeile 252, nicht 271). F4 korrekt (Zeile 127, nicht 111). F5 korrekt, teils gedeckt. F6 Duplikat. Keine Falschmeldung mit Substanz. | keine |

Vergleich: #970 hatte keinen menschlichen und keinen CI-Review-Kommentar; F2 wäre sonst
nicht aufgefallen.

## Beobachtungen

- Kimi K2.7 Code denkt sichtbar und lang („Thought“-Blöcke). Der Operator hielt das bei
  Aufgabe 2 für eine Schleife und brach mit Strg-C ab; die Arbeit war zu dem Zeitpunkt
  fertig (Edit, pytest, ruff, diff). Ein Durchgang endet, wenn die Eingabezeile zurückkommt.
- Zeilenangaben aus Reviews sind ungenau (2 von 3 daneben), Dateien und Aussagen stimmen.
- Reviews sind cache-lastig (978k Cache-Lesungen bei 26 Schritten): ein Diff je Sitzung.
- OpenCode zeigt für den Custom-Provider immer 0,00 USD; echte Beträge nur in den
  Anbieterkonsolen.
- Die globale OpenCode-Konfiguration trägt zusätzlich `ollama/kai-qwen3-coder:30b-16k`
  (lokale Reserve, seit 14.09. vormittags). Im Worktree gewinnt die Projektkonfiguration
  beim Modell; die Permission-Regeln gelten für beide Provider.

## Einstufung (Operator, 2026-09-14: „Einstufung so“)

| Route | Einstufung | Auflagen |
|---|---|---|
| `kai-dev-code` (Kimi K2.7 Code) | **freigegebene Entwicklerreserve** | Zeilenangaben aus Reviews nachprüfen; ein Diff je Review-Sitzung; Modell für schreibende Sitzungen gepinnt |
| `kai-dev-economy` (DeepSeek V4.1 Flash) | **freigegebene Analyse-Reserve** (nur lesend) | — |
| `kai-dev-frontier` (Kimi K3) | **geprobt** (Smoke PASS 2026-09-14 ~17:03Z: HTTP 200, 0,000396 USD je 8-Token-Probe, Preis bekannt), Freigabe je Aufgabe | nie Standardmodell; nach jeder Aufgabe zurück auf `kai-dev-code` |

Folgeauftrag aus F2: Datums-Cutoff bzw. Schutz neuer Innenzeilen ohne Außenzeile in
`app/ai/spend.py::_altpaare_entfernen` (eigenes Issue).
