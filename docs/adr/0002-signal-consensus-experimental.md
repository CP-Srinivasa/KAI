# ADR 0002 — Signal-Consensus als `@experimental` markiert

- **Datum**: 2026-04-24
- **Status**: Experimental (pausiert bis PHASE 5 Re-Entry 2026-05-16)
- **Kontext**: Neo-Meta-Audit NEO-F-META-20260424-003
- **Entscheidung**: D-186 (Multi-Agent-Gate Re-Eval, 2026-04-24)

## Kontext

`app/trading/signal_consensus.py` (298 LoC + 331 LoC Tests = 629 LoC) implementiert einen Multi-Modell-Consensus-Validator: mehrere LLMs bewerten dieselbe Signal-Situation unabhängig, ALLE müssen übereinstimmen (unanimous, fail-closed) bevor ein Trade durchgeht.

Konzept war der technische Unterbau für das Multi-Agent-Modell (Codex als unabhängiger Signal-Validator), das unter D-117 (2026-04-04) pausiert wurde.

## Aktueller Stand (2026-04-24)

- CLI-Flag `--consensus` default `False` — Validator wird nie aktiv gerufen
- Call-Site `_build_consensus_validator` in `app/orchestrator/trading_loop.py:818-845` gibt ohne Flag immer `None` zurück
- D-186 (heute) hat entschieden: **keine Reaktivierung** von Codex/Antigravity bis PHASE 5 Re-Entry 2026-05-16
- Grund (aus D-186): Signal-Precision ist nicht der Bottleneck (Active 48%, über 30%-Gate); Bottleneck sind Signal-Menge + Execution-Fenster — beides nicht durch Consensus adressiert

## Entscheidung

Das Modul **nicht löschen**, aber klar als `@experimental` kennzeichnen:

1. Modul-Docstring in `app/trading/signal_consensus.py` erhält prominente `EXPERIMENTAL`-Markierung mit Verweis auf diese ADR
2. CLI-Help für `--consensus` wird um `(EXPERIMENTAL — siehe docs/adr/0002)` ergänzt beim nächsten Touch
3. Keine neuen Features im Modul bis Binary-Entscheidung am 2026-05-16
4. Tests bleiben erhalten (sichern Contract falls reaktiviert)

## Binary-Entscheidung am 2026-05-16 (PHASE 5 Re-Entry)

Am Re-Entry-Stichtag eine der drei Optionen wählen:

- **Aktivieren**: wenn es einen konkreten Execution-Pfad gibt, der einen Second-Opinion-Validator sinnvoll macht (z.B. High-Value-Trades mit größeren Positionen); dann CLI-Flag default aktivieren, Produktions-Rollout planen, diese ADR auf `accepted` setzen.
- **Extrahieren**: in separaten Branch `experimental/signal-consensus` verschieben, aus `main` entfernen; reduziert Wartungslast; diese ADR auf `rejected` setzen.
- **Löschen**: Code + Tests vollständig entfernen; diese ADR auf `deprecated` setzen.

Status-quo (Experimental-Dauerlager in main) ist **nicht** eine der Optionen — muss am 2026-05-16 entschieden werden.

## Folgen

- Wartungslast niedrig (629 LoC, bewegt sich selten)
- Imports in `trading_loop.py` bleiben — keine Breaking-Change im Call-Graph
- `@experimental`-Kennzeichnung signalisiert neuen Contributern: "nicht erweitern, nicht refaktorisieren, Binary-Decision steht an"

## Verweise

- `DECISION_LOG.md` D-186 (Multi-Agent-Gate Re-Eval)
- `DECISION_LOG.md` D-117 (Original Multi-Agent-Pause)
- `artifacts/agents/neo/findings.jsonl` NEO-F-META-20260424-003
- `docs/phase5_re_entry.md` (Re-Entry-Playbook)
