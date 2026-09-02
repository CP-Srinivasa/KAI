# KAI Inference Gateway — Final Evidence Report

**Verify date:** 2026-09-02

**Base:** `3b0fa02751728a93e8c2c34a9bb6515a0dff2597`

**Implementation commit:** `12a7add2`

**Branch:** `codex/llm-router-migration-20260901`

## Ergebnis

Der Repository-Scope ist code-complete und deployment-gated. Default ist `off`; kein Pi,
kein Production-Service, keine Mainline und kein Live-Provider wurden verändert. Reale
Gateway-/Providerqualität und Pi-Start bleiben bis zum Operator-Shadow `NOT_PROVEN`.

## Ausgeführte Quality Gates

| Gate | Ergebnis |
|---|---|
| Fokusmatrix Router/Settings/Provider/Text/STT/Consensus | 102 passed |
| Erweiterte Architektur-/Failure-/Deployment-Ratchets | 156 passed, 1 skipped |
| Risikobasierte Regression: Analysis, Pipeline, Provider, API/Dashboard, Telegram, Security, Paper Execution, Trading Loop | 1177 passed, 2 skipped |
| Vollständiger Unit/Integration/Verifier-Lauf auf Windows, 4 Worker | 9036 passed, 39 skipped, 2 xfailed, 97 failed |
| Ruff lint | all checks passed |
| Ruff format check | all files formatted |
| Mypy strict | 695 source files, no issues |
| `git diff --check` vor Sign-off | passed nach Entfernung zweier Markdown-Trailing-Spaces |
| Pre-commit Secret Scanner | no secrets detected |
| zusätzlicher changed-file Secret Regex Scan | 0 suspicious files |

Die 97 Full-Suite-Fehler bilden einen gemeinsamen Plattformblock: Windows übergibt
`C:\...`-Pfade an `/bin/bash`, das diese Dateien nicht findet. Betroffen sind vorhandene
Pi-Installer-/Freeze-/Deploy-/Cutover-Shelltests. Zwei darin sichtbar gewordene, durch den
Sprint verursachte Ratchets waren unabhängig reproduzierbar: die neue Unit brauchte
`OnFailure=`, der neue Shadow-Strom einen Consumer-Vertrag. Beide wurden behoben und danach
im 156-Test-Ratchet-Satz grün verifiziert. Ein vollständig grüner Linux-Gesamtlauf ist in
dieser Windows-Session daher `NOT_PROVEN`; die betroffenen Shellpfade wurden nicht passend
gefälscht oder übersprungen.

## Security- und Default-Evidenz

- `InferenceSettings.enabled == false`, `mode == off`, `effective_mode == off` getestet.
- Gateway-URL default `http://127.0.0.1:4000/v1`; nicht-loopback ohne expliziten Override
  wird beim Settings-Load abgewiesen.
- systemd startet LiteLLM mit `--host 127.0.0.1`, `User=ubuntu`, leerem Capability Set,
  `NoNewPrivileges=true` und Operator-`OnFailure`.
- `/etc/kai/litellm.env`: dokumentiert `root:ubuntu`, `0640`; die Unit enthält nur eine
  `EnvironmentFile`-Referenz, keine Werte.
- Gateway-Secret ist aus `repr` ausgeschlossen; Status liefert nur Readiness-Bools.
- Telemetrie und Shadow speichern keine Prompts, Dokumenttexte oder Exception-Texte.
- `KAI_LLM_*`, `app/intelligence/*` und ADR 0015 haben keinen Diff; deren
  `influences_execution=false`-Vertrag bleibt getrennt.
- Signal Consensus nutzt Gateway ausschließlich als Shadow; Artifact-Vertrag erzwingt
  `authoritative=current` und `influences_execution=false`.

## Re-Inventur direkter Provider-Clients

Direkte Clients verbleiben absichtlich in:

- `app/integrations/openai/provider.py`, `anthropic/provider.py`, `gemini/provider.py`,
  `xai/provider.py`: `off`-Rollback, direkter Primary-Rückfall und unabhängiger
  Anthropic-Shadow; xAI bleibt Provider `xai`.
- `app/messaging/text_intent.py` und `kai_chat_engine.py`: unveränderter direkter
  Rollback/Rückfall, zentral über `run_inference_mode` orchestriert.
- `app/inference/stt.py`: eigene STT-Abstraktion mit direktem Whisper-Rückfall.
- `app/trading/signal_consensus.py`: direkte autoritative Validatoren; Gateway nur Shadow.
- `app/intelligence/providers.py`: ADR-0015-Scope, bewusst ausgeschlossen.

Alle späteren Primary-Gateway-Provider-/Modell-Fallbacks für Text laufen zentral in
`InferenceRouter`; Attempt-Cap, Retry, Circuit, Budget, Schema und Audit werden nicht in
Business-Modulen dupliziert.

## Drift- und Produktionsnachweis

Remote-Default war beim Sign-off `9293c4239b80ebbfec42a39cda289ba4f60a1610` und seit der
Basis um eine Datei gewachsen. Schnittmenge mit den 60 Sprint-Dateien: `0`; ein Rebase war
nach der Repo-Regel nicht erforderlich. Production Switch: `NOT_EXECUTED`. Merge:
`NOT_EXECUTED`. SSH-Schreibzugriff: `NOT_EXECUTED`. Push: `NOT_EXECUTED`.
