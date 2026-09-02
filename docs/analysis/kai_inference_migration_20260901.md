# KAI Inference Migration Report — 2026-09-01

## Ausgangspunkt

Basis ist Commit `3b0fa02751728a93e8c2c34a9bb6515a0dff2597`; vollständige Vorab-Inventur:
`docs/analysis/kai_inference_inventory_20260901.json`. Die gemessene Pi-Baseline wurde
nicht verändert. Es gab keinen SSH-Schreibzugriff, keinen Deploy und keinen Mainline-Merge.

## Migrierte operative Aufrufer

- Analysis-Factory und Pipeline: off identisch direkt; shadow parallel und nicht
  autoritativ; primary über Route `standard` mit direktem Legacy-Rückfall.
- RSS/News/YouTube/OKX/Messari/X-Persistenz: tatsächlicher Provider, Modell und Route statt
  neu erzeugtem generischen `unknown` soweit der Call es belegt.
- Telegram Text Intent: strikter KAI-Contract; `standard`; keine Erweiterung der Command-
  Allowlist oder Admin-/Approval-Rechte.
- KAI Web Smalltalk: `standard`; direkter OpenAI-Pfad bleibt Rollback/Fallback.
- Telegram und Web Voice: eigener `SpeechToTextProvider`, Route `stt`, direkter Whisper-
  Rückfall, bestehende Download-/Upload-Grenzen unverändert.
- Signal Consensus: nur `critical` Shadow-Vergleich; bestehende direkte Validatoren,
  Unanimity, Fail-closed und resultierende Confidence bleiben autoritativ.
- Read-only Status: Modus, Reachability, Route-Aliase, readiness booleans, Circuit,
  24h-Telemetrie und Budget; keine Mutations- oder Trading-Control-API.

## Bewusst verbleibende direkte Provider-Aufrufe

- `app/integrations/{openai,anthropic,gemini,xai}/provider.py`: exakter `off`-Rollback und
  primary Legacy-Fallback; Anthropic zusätzlich unabhängiger direkter Shadow. Diese Clients
  zu löschen würde die verlangte Ein-Schalter-Rückkehr und Vergleichsunabhängigkeit brechen.
- `app/messaging/text_intent.py`, `app/messaging/kai_chat_engine.py`: direkter OpenAI-
  Rollback/Fallback bei `off` bzw. erschöpftem Gateway.
- `app/inference/stt.py`: direkter OpenAI Whisper Rollback/Fallback.
- `app/trading/signal_consensus.py`: direkte OpenAI-kompatible Validatoren bleiben
  autoritativ; eine Primary-Migration ist mangels Behaviour-Evidenz `DEFERRED`.
- `app/intelligence/providers.py`: vollständig außerhalb des Sprint-Scope; ADR 0015 bleibt
  getrennt, shadow-only und ohne Execution Authority.

Direkte SDK-Präsenz bedeutet daher nicht Umgehung des neuen Vertrags: Sie ist die bewusst
beibehaltene Default-/Fallback-Grenze. Im späteren `primary`-Betrieb laufen die migrierten
autoritativen Textpfade zuerst über `app/inference`.

## Relevance Gate und bestehender Shadow

`SOURCE_CRYPTO_RELEVANCE_GATE_MODE` wurde nicht dupliziert und bleibt default `shadow`.
Automatisches Umschalten auf `enforce` wäre ohne Evidenz unzulässig. Gesparte Calls werden
in diesem Sprint nicht als erfundene Kosten ausgewiesen (`DEFERRED`), weil beim Skip weder
tatsächliche Token noch ein belegter Modellpreis existieren. Der separate Anthropic-Shadow
bleibt direkt und logisch von Gateway-Shadow-Rollen getrennt.

## Deployment und Dashboard

Production-fähige, aber nicht aktivierte LiteLLM-Konfiguration, ein gepinntes separates
Environment, lokaler Healthcheck und `kai-litellm.service` liegen unter `deploy/` bzw.
`scripts/`. `kai-server.service` wurde absichtlich nicht abhängig gemacht. Die bestehende
SPA wurde nicht umgebaut; die vollständige Operator-Sicht steht als bestehend geschützter
read-only Dashboard-API-Endpunkt bereit. Ein spezielles neues Frontend-Panel ist `DEFERRED`.

## Bekannte Risiken und Evidenzgrenze

- Kein echter Provider- oder LiteLLM-Call wurde ausgeführt; reale Modellkompatibilität,
  Antwortqualität, Gateway-Kostenheader und Pi-Systemd-Start sind bis zum kontrollierten
  Operator-Shadow `NOT_PROVEN`.
- Circuit-State ist absichtlich process-lokal und wird bei Restart zurückgesetzt.
- KAI Budget-State liest append-only Telemetrie; fehlende/korrupte Kosten bleiben unbekannt
  und werden nicht als Null interpretiert.
- Live-Preise sind nicht hardcodiert. Ohne Gateway-Kosten oder operatorgepflegte Metadaten
  bleibt Cost Attribution `null`.
- Die Status-API zeigt die Oberfläche; visuelle SPA-Integration bleibt offen.
