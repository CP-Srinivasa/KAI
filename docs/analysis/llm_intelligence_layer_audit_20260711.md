# Repo-Audit: LLM-/Local-Model-/RAG-Bestand (Phase 0 „KAI Local Intelligence Layer")

**Datum:** 2026-07-11 · **Basis:** Mainline `c6af4be`+ (Worktree `feat/llm-foundation-p0`) · **Scope:** Code, Tests, Doku, Konfiguration, git-Historie.
**Zweck:** Bestandsaufnahme VOR dem Bau einer lokalen, fail-closed LLM-Schicht (ADR 0015). Read-only; keine Fixes in diesem Dokument.

---

## 1. Gesamtbild

- Es existiert eine **saubere provider-agnostische Analyse-Schicht** (Tier-3, News-Analyse): `BaseAnalysisProvider` + strict-Pydantic `LLMAnalysisOutput` (`app/analysis/base/interfaces.py:11,54`), String-Registry `create_provider` (`app/analysis/factory.py:14`), first-success-Fallback `EnsembleProvider` (`app/analysis/ensemble/provider.py:29`), fail-closed Baseline ohne Key `InternalModelProvider` (`app/analysis/internal_model/provider.py:92`), Primary+Shadow-Harness `AnalysisPipeline` (`app/analysis/pipeline.py:422,651`).
- **Kein RAG, keine Embeddings, keine Vektor-DB, kein torch/transformers** — weder live noch in `pyproject.toml` (Deps: `openai`, `anthropic`, `google-genai`, `tiktoken`, `mcp`). „embedding"-Treffer sind False-Positives (Regex-Kommentar; expliziter Verzicht in `app/analysis/source_confluence.py:33`).
- **Ollama kommt heute nirgends im Live-Code vor.** Ein früherer lokaler Modell-Pfad existierte: das **Companion-ML-System** (Distillation/Training/Tuning/Shadow/Eval, ~8.300 LOC + ~21.400 Testzeilen), eingeführt `6cc3a795`, per **D-107 / Commit `73bde122` (2026-03-24)** aus main entfernt und auf Branch `companion-ml` archiviert. Überlebt haben nur `docs/archive/*`-Contracts (Ollama/llama.cpp/vLLM/GGUF dort als EXTERNE Fine-Tuning-Tools) und eine **unverdrahtete Reliquie `COMPANION_MODEL_ENDPOINT` in `.env.example:79-81`** (kein Settings-Feld, kein Code-Verweis).
- **Ein Task-Router existiert nicht.** Fallback ist ausschließlich failure-getriggert (Ensemble), kein Routing nach Task-Typ/Kosten/Fähigkeit, kein Provider-Health-/Budget-Tracking.

## 2. Vorhandene Komponenten (live)

| Komponente | Pfad | Status |
|---|---|---|
| Output-Contract (strict Pydantic) | `app/analysis/base/interfaces.py:11` | LIVE, wiederverwendbar als Muster |
| Provider-ABC | `app/analysis/base/interfaces.py:54` | LIVE |
| Provider-Registry | `app/analysis/factory.py:14` | LIVE (Zweige: internal/openai/anthropic/gemini/grok/ensemble) |
| Fallback-Kette (first-success) | `app/analysis/ensemble/provider.py:29` | LIVE |
| Fail-closed-Baseline ohne Key | `app/analysis/internal_model/provider.py:92` (`rule-heuristic-v1`) | LIVE; dokumentierter Upgrade-Pfad auf lokales Modell (`:12`) |
| Shadow-Harness (Primary+Shadow, Divergenz-Report) | `app/analysis/pipeline.py:651` | LIVE |
| Prompts versioniert (Namenskonvention `_V1`) | `app/analysis/prompts.py:9` | LIVE; keine Registry |
| Externe Provider | `app/integrations/{openai,anthropic,gemini,xai}/provider.py` | LIVE (xAI default-off) |
| Business-Rule-Validierung | `app/analysis/validation.py:13,49` | LIVE (non-raising + Clamp) |
| Settings/Keys | `app/core/settings.py:196` `ProviderSettings` (`repr=False`, `_strip_secret`) | LIVE |

## 3. Tote / unvollständige Implementierungen

1. **Companion-ML** — entfernt (`73bde122`, D-107); nur `docs/archive/*` + Branch-Archiv. Lehre: extern gedachtes Fine-Tuning + Promotion-Automatik („Companion rückt als primärer Provider nach") war zu groß, zu unklar konsumiert, nicht falsifizierbar — genau das Anti-Muster, das ADR 0015 ausschließt.
2. **`COMPANION_MODEL_ENDPOINT`** — `.env.example:79-81` verspricht Startup-Reject externer URLs; es gibt weder Settings-Feld noch Reject-Code. Reliquie → Aufräum-Kandidat (in PR-1 entfernt).
3. **`SignalConsensusValidator`** (`app/trading/signal_consensus.py`) — EXPERIMENTAL/pausiert (ADR 0002), in Prod nie konstruiert (`--consensus` default False), hartes OpenAI-SDK, freies `json.loads` ohne Schema. Nicht erweitern; bei Bedarf Neubau auf Provider-Abstraktion.
4. **Doku-Drift:** `app/analysis/pipeline.py:29-35` beschreibt „confidence-weighted averaging" — nicht implementiert (Ensemble ist first-success).

## 4. Sicherheits-/Architekturprobleme (Findings, nicht Teil von PR-1)

| # | Finding | Anker | Risiko |
|---|---|---|---|
| F-1 | `text_intent.py` postet **roh via httpx** an `api.openai.com` — vorbei an Provider-Abstraktion, Retry-/Timeout-/Schema-Konventionen | `app/messaging/text_intent.py:97,134` | Architektur-Drift; CLAUDE.md:483 verletzt |
| F-2 | `kai_chat_engine.py` verdrahtet `AsyncOpenAI` direkt (Chat + Whisper) | `app/messaging/kai_chat_engine.py:225,346` | dito (bewusste Ausnahme? undokumentiert) |
| F-3 | Consensus-Validator: LLM-Antwort ohne Schema-Zwang (`json.loads` frei) | `app/trading/signal_consensus.py:273` | tot, aber falls reaktiviert: unvalidierter LLM-Output nahe Trading-Pfad |
| F-4 | Doppelte Ensemble-Konstruktion (Factory UND CLI) | `app/analysis/factory.py:62` vs `app/cli/main.py:60-92` | Divergenz-Risiko |
| F-5 | Observability-Lücke: LLM-failure-rate/latency p95 nicht implementiert (B-002) | `app/core/settings.py:1662` | blinder Fleck |
| F-6 | `.env.example`-Reliquie `COMPANION_MODEL_ENDPOINT` | `.env.example:79-81` | Verwirrung/Scheinsicherheit („rejected at startup" stimmt nicht) |

## 5. Wiederverwendungspotenzial für die neue Schicht (bindend, siehe ADR 0015 §4)

- **Muster** aus der Analyse-Schicht: Registry-Dispatch, first-success-ohne-stillen-Cloud-Wechsel, fail-closed-Baseline (→ NoOpProvider), `xai/provider.py` als Vorlage „OpenAI-kompatibel mit base_url" (→ OllamaProvider), tenacity-Retry, `ProviderSettings`-Idiom (`repr=False`).
- **Querschnitt:** Secret-Redaction `app/audit/sanitization.py:182,193` (`sanitize_value`, Marker `[REDACTED:*]`) · JSON-Schema-Validator `app/core/schema_runtime.py:51` (`validate_json_schema_payload`, Draft202012, fail-closed) · append-only-Writer-Muster „frozen Pydantic → `append_lock` → append" (`app/execution/paper_engine.py:1531`, `app/core/file_lock.py:27`) · tolerant-Reader `app/storage/jsonl_io.py:45,121` · Hash-Ketten-Blaupause `app/audit/structured_reasoning.py:79-258` · Pfad-Guards `app/agents/tools/_helpers.py:52,76` (`resolve_workspace_path`, `require_artifacts_subpath`) · Startup-Secret-Check `app/security/secrets.py:36` · Shadow-Job-Vorlage `scripts/shadow_real_feed_tick.py` + `deploy/systemd/kai-shadow-real-feed.{service,timer}` (installiert-aber-nicht-enabled).
- **Settings-Doppelflag-Idiom:** `enabled=False` + `shadow_only=True` (`app/core/settings.py:296,338`).

## 6. Regel-Anker (CLAUDE.md)

- `CLAUDE.md:483` „Use provider abstraction" · `:484` „structured outputs with schema validation" · `:485` „No direct business logic inside transport/provider clients" · `:675` „never hardwire business logic into LLM provider clients".

## 7. Konsequenz

Zielarchitektur, Invarianten, Gates und Diagramm → **ADR 0015 (`docs/adr/0015-kai-local-intelligence-layer.md`)**. Kernentscheid: neue generische Task-Schicht `app/intelligence/` (Task-Typ → Schema → Provider), bestehende Analyse-Schicht bleibt unangetastet; Wiederverwendung der o.g. Muster statt Neubau; F-1..F-5 bleiben dokumentierte Findings für spätere, separate Aufträge.
