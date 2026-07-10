# ADR 0015 — KAI Local Intelligence Layer (lokales LLM als auditierbare Shadow-Schicht)

**Status:** ACCEPTED (2026-07-11) · **Kontext-Doku:** `docs/analysis/llm_intelligence_layer_audit_20260711.md` · **Bezug:** ADR 0012 (Truth-Plattform), ADR 0014 (Schichtenkarte/Gates), D-107 (Companion-ML-Entfernung), CLAUDE.md §LLM Integration (:483-485)

## 1. Kontext

KAI soll ein lokales LLM (Ollama auf Operator-Hardware; verifiziert: Ollama 0.22.1, `qwen3-coder:30b`, `deepseek-r1:8b`, OpenAI-kompatibler `/v1`-Endpoint) als **austauschbare, vollständig auditierbare Intelligence-Schicht** nutzen können — für read-only-Aufgaben wie Daily-Review-Zusammenfassungen, Anomalie-Erklärungen und Doku-Q&A. KAI Core, Truth Layer, Risk Gates und Execution bleiben deterministisch.

Vorbedingungen aus dem Audit:
- Eine provider-agnostische **Analyse**-Schicht existiert (Tier-3 News-Analyse, `BaseAnalysisProvider`/`LLMAnalysisOutput`/Factory/Ensemble/Shadow-Harness). Ihr Contract ist domänen-spezifisch (sentiment/impact) — für generische Tasks ungeeignet, als Muster-Fundus exzellent.
- Ein früherer lokaler Modell-Pfad (**Companion-ML**, inkl. `localhost:11434`-Endpoint-Idee) wurde per D-107 bewusst entfernt: extern gedachtes Fine-Tuning, Promotion-Automatik Richtung Primär-Provider, kein falsifizierbarer Nutzen-Nachweis.
- Kein Task-Router, kein Ollama-Code, keine ML-Heavy-Deps im Repo.

## 2. Entscheidung

Wir bauen **`app/intelligence/`** — eine generische, task-basierte LLM-Schicht mit diesen Eigenschaften:

1. **Shadow-only by construction:** Ausgaben sind `untrusted_analysis`; sie landen ausschließlich als gekennzeichnete Artefakte/Report-Blöcke unter `artifacts/` und können strukturell keine Trades, Gates, Env-Flags oder Deploys auslösen.
2. **Fail-closed by default:** `KAI_LLM_ENABLED=false`, `KAI_LLM_MODE=disabled`, `KAI_LLM_PROVIDER=none`, `KAI_LLM_INFLUENCES_EXECUTION=false`. `influences_execution` ist eine **Konstante der Schicht** — ein Boot-Validator REFUSED `true` (kein legitimer Konfigurationszustand).
3. **Austauschbare Provider hinter EINEM Interface:** `NoOpProvider` (immer da, antwortet „disabled"), `MockProvider` (deterministische Fixtures für Tests), `OllamaProvider` (OpenAI-kompatible `/v1/chat/completions` via httpx, `base_url` aus Settings), optional `ClaudeProvider` (offizielles `anthropic`-SDK, lazy import, NUR bei explizitem `KAI_LLM_PROVIDER=claude`).
4. **Kein stiller Cloud-Fallback:** Provider-Wechsel passiert NIE automatisch. Fehler ⇒ `LLMResult(ok=False, fallback_reason=…)`, nie Provider-Substitution, nie Exception in den Aufrufer.
5. **Schema-Zwang:** Jeder `task_type` hat ein JSON-Schema; Validierung über den bestehenden `validate_json_schema_payload` (Draft202012, fail-closed). Nicht-validierende Antworten ⇒ `malformed_json`/`schema_violation`, verworfen.
6. **Voll-Audit:** Jeder Call (auch Fehlschläge) schreibt append-only nach `artifacts/intelligence_audit.jsonl`: `request_id, ts, task_type, provider, model, prompt_hash(sha256), input_refs, latency_ms, ok, fallback_reason, confidence, evidence, redaction_count, influences_execution=false(konstant)`. Writer nach dem kanonischen Muster (frozen Pydantic `extra="forbid"` → `append_lock` → append); Leser via `iter_jsonl_tolerant`.
7. **ContextBuilder mit Pfad-Allowlist:** nur `artifacts/daily_strategy/`, `artifacts/agents/daily_review/`, `docs/adr/`, `docs/runbooks/` (Settings-erweiterbar); `resolve()`+`is_relative_to`-Guards nach `_helpers.py`-Vorbild; Denylist hart verdrahtet (`.env*`, `config/`-Secrets, `*.session`, Macaroons, Keys). Jeder Prompt läuft VOR dem Versand durch `sanitize_value` (Secret-Redaction, Zähler ins Audit).
8. **Keine automatische Modellinstallation:** `KAI_LLM_MODEL` leer ⇒ `unavailable`. Kein `ollama pull`, kein Download-Code.

### Zielarchitektur

```
                        ┌──────────────────────────────────────────────┐
   read-only Quellen    │            app/intelligence/                 │      artifacts/
  (Allowlist+Denylist)  │                                              │
  daily_strategy/  ─────┼─▶ ContextBuilder ──▶ TaskRouter ──▶ Provider ┼──▶ intelligence_audit.jsonl
  daily_review/         │   (Pfad-Guards,      (task_type →   NoOp     │    (append-only, prompt_hash,
  docs/adr|runbooks/    │    sanitize_value)    Schema+Modell) Mock    │     influences_execution=false)
                        │                                      Ollama  │
                        │        Schema-Validierung ◀────────  Claude* │──▶ llm_shadow_notes/*.md
                        │        (validate_json_schema_payload)        │    (gekennzeichnet: untrusted)
                        └──────────────────────────────────────────────┘
   * nur explizit KAI_LLM_PROVIDER=claude — nie als Fallback

   VERBOTEN (Import-Invariante, testdurchgesetzt, beide Richtungen):
   app/execution · app/risk · app/orchestrator · app/signals · app/trading
```

### Interface (Kern)

```python
LLMRequest(task_type, prompt, schema, input_refs, max_tokens, timeout_s)
LLMResult(ok, data|None, provider, model, latency_ms, fallback_reason|None, confidence|None)
class LLMProvider(Protocol):
    name: str
    def complete(self, request: LLMRequest) -> LLMResult: ...
    def available(self) -> bool: ...        # Health ohne Modell-Call (z.B. GET /api/version)
```

### Settings (pydantic-settings, nested, `env_prefix="KAI_LLM_"`)

`enabled=False · mode: Literal["disabled","shadow"]="disabled" · provider: Literal["none","mock","ollama","claude"]="none" · influences_execution=False (Boot-Refuse bei true) · ollama_base_url="http://localhost:11434" · model="" · context_allowlist=[…] · timeout_s=120 · max_tokens=2048` — API-Key-Felder mit `repr=False`; Claude-Key kommt aus dem bestehenden `ProviderSettings.anthropic_api_key`.

## 3. Verbote (Invarianten, in Phase 1/2 test-durchgesetzt)

- Kein Import-Pfad zwischen `app/intelligence/` und Execution-/Risk-/Orchestrator-/Signals-/Trading-Modulen (AST-Test, beide Richtungen).
- Keine Secrets/.env/Credentials im Kontext (Denylist-Test + Traversal-Test `..`/Symlink/absolut).
- Kein stiller Cloud-Fallback (Test: Ollama down ⇒ `fallback_reason="unavailable"`, Provider bleibt „ollama").
- Keine automatische Modellinstallation (kein Download-Code; leeres Modell ⇒ unavailable).
- Kein Pi-Deploy in diesem Arbeitsstrang; systemd-Units werden vorbereitet, aber nicht enabled/deployed.
- LLM-Ausgaben ändern nie Truth-Layer-Artefakte (prereg/verdicts/ledger) — Schreibziel ausschließlich neue, klar gekennzeichnete Shadow-Artefakte.

## 4. Wiederverwendung statt Neubau (bindend)

`sanitize_value` (Redaction) · `validate_json_schema_payload` (Schemas) · `append_lock`+frozen-Pydantic-Writer · `iter_jsonl_tolerant` (Reader) · `structured_reasoning`-Hash-Ketten-Blaupause (optional ab Phase 2) · `resolve_workspace_path`/`require_artifacts_subpath`-Muster (Pfad-Guards) · `validate_secrets` (Key-Check) · tenacity-Retry-Muster · `xai/provider.py` als OpenAI-kompatible-Vorlage · Doppelflag-Idiom aus `settings.py:296` · `shadow_real_feed_tick.py`+systemd-Paar als Job-Vorlage.

## 5. Abgegrenzte Alternativen (verworfen)

- **Analyse-Schicht erweitern (Use-Cases in `LLMAnalysisOutput` pressen):** falscher Contract (sentiment/impact ≠ summary/explain/QA); würde Live-News-Pfad anfassen. Verworfen. Ein `OllamaAnalysisProvider` für die News-Pipeline bleibt möglich — separater, späterer Registry-Eintrag.
- **Companion-ML reaktivieren:** D-107-Gründe unverändert (Fine-Tuning-Automatik, Promotion-Ambition, kein falsifizierbarer Nutzen). Diese ADR ist das GEGENTEIL: kein Training, keine Promotion, read-only Shadow mit Audit.
- **LangChain/Framework:** verboten durch „no large dependencies without strong reason" + Provider-Lock-in-Regel; httpx+SDKs reichen.

## 6. Abgrenzung zu D-107 (warum diesmal anders)

Companion-ML wollte ein lokales Modell **in den Entscheidungs-Pfad befördern** (Promotion zum Primär-Provider) und brauchte dafür Training/Distillation/Eval-Maschinerie. Diese Schicht ist strukturell nicht promotierbar: `influences_execution` ist konstant false, Konsumenten sind Menschen (Reports), die Ausgaben sind als untrusted markiert, und der einzige Erfolgs-Maßstab ist der Eval-Plan (§7) — fällt er durch, wird die Schicht wieder entfernt (ein PR, da isoliertes Modul).

## 7. Evaluation & GO/NO-GO

- **Golden Dataset:** ≥20 fixierte Fälle (10 Daily-Summaries aus echten, redigierten `daily_strategy`-Dateien; 5 Timer-/Source-Anomalien mit bekannter Ursache; 5 ADR-/Runbook-Fragen mit referenzierter Antwort). Deterministische JSON-Fixtures im Repo (`tests/fixtures/llm/`).
- **Metriken:** Schema-Validierungsquote, Halluzinationsquote gegen `evidence`-Refs (Stichprobe Operator), Latenz p50/p95 lokal, Redaction-Treffer (muss 0 Leaks sein — Test mit gepflanzten Fake-Secrets).
- **GO (Phase 2 → dauerhafter Shadow-Betrieb):** Schema-Quote ≥95% · 0 Secret-Leaks · Operator bewertet ≥70% der Summaries als „nützlich oder neutral" · kein Invarianten-Test rot.
- **NO-GO:** eines der Kriterien verfehlt nach 2 Iterationen ⇒ Schicht bleibt disabled oder wird entfernt; Verdikt wird wie üblich dokumentiert (kein stilles Weiterlaufen).

## 8. Konsequenzen

- **PR-1 (dieses Dokument + Audit):** docs-only. Entfernt zusätzlich die tote `COMPANION_MODEL_ENDPOINT`-Reliquie aus `.env.example` (Audit F-6).
- **PR-2 (Phase 1):** `app/intelligence/` Foundation + Settings + Audit-Trail + Contract-/Security-Tests. Kein Konsument.
- **PR-3 (Phase 2):** 3 Shadow-Use-Cases (CLI, read-only) + Injection-/Traversal-/Redaction-Tests + Golden-Dataset-Fixtures + lokaler Smoke gegen Ollama (Workstation). systemd-Paar vorbereitet, nicht enabled.
- Findings F-1..F-5 des Audits (Abstraktion-Umgehungen, Doku-Drift, Observability-Lücke) bleiben eigenständige, spätere Aufträge — bewusst nicht Teil dieses Stranges.
