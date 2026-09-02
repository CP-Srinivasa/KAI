# Runbook — KAI operatives Inference Gateway

Dieses Runbook beschreibt einen späteren Operator-Cutover. Der Sprint selbst installiert,
startet oder aktiviert nichts auf dem Pi. Ausgangszustand und sicherer Default sind
`KAI_INFERENCE_ENABLED=false` und `KAI_INFERENCE_MODE=off`.

## 1. Voraussetzungen und Installation

Auf dem Pi im autoritativen Checkout `/home/kai/ai_analyst_trading_bot`:

```bash
python3 -m venv .venv-litellm
.venv-litellm/bin/pip install -r deploy/litellm/requirements.txt
sudo install -o root -g ubuntu -m 0640 deploy/litellm/litellm.env.example /etc/kai/litellm.env
sudo install -o root -g root -m 0644 deploy/systemd/kai-litellm.service /etc/systemd/system/kai-litellm.service
sudo systemctl daemon-reload
```

`requirements.txt` pinnt die reviewte LiteLLM-Version. Ein Upgrade ist ein eigener
Konfigurations-/Smoke-Test, kein unbeaufsichtigtes `latest`.

## 2. Secrets

`/etc/kai/litellm.env` muss `LITELLM_MASTER_KEY` und nur die tatsächlich genutzten
Provider-Keys enthalten. Erwartete Namen: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `XAI_API_KEY`. Zukunftsprovider bleiben ohne Konfig-Eintrag inaktiv.

```bash
sudoedit /etc/kai/litellm.env
sudo chown root:ubuntu /etc/kai/litellm.env
sudo chmod 0640 /etc/kai/litellm.env
sudo stat -c '%U:%G %a %n' /etc/kai/litellm.env
```

In KAI `.env` wird derselbe Master-Key nur als `KAI_INFERENCE_GATEWAY_API_KEY` gesetzt.
Weder Werte noch Präfixe in Tickets, Logs oder Chat kopieren.

## 3. Start und Health

```bash
sudo systemctl start kai-litellm.service
systemctl status kai-litellm.service --no-pager
journalctl -u kai-litellm.service -n 100 --no-pager
/home/kai/ai_analyst_trading_bot/.venv/bin/python scripts/litellm_healthcheck.py --url http://127.0.0.1:4000
curl --fail --silent --show-error http://127.0.0.1:8000/dashboard/api/inference
```

Erwartet: Bind nur auf `127.0.0.1:4000`, Gateway reachable, keine Secret-Werte im Status.
Der KAI-Server bleibt auch bei gestopptem Gateway im Modus `off` startfähig.

## 4. Shadow

Zuerst `.env` setzen und nur KAI neu starten:

```text
KAI_INFERENCE_ENABLED=true
KAI_INFERENCE_MODE=shadow
KAI_INFERENCE_GATEWAY_URL=http://127.0.0.1:4000/v1
KAI_INFERENCE_GATEWAY_API_KEY=<same value as LITELLM_MASTER_KEY>
```

```bash
sudo systemctl restart kai-server.service
systemctl status kai-server.service kai-litellm.service --no-pager
curl --fail --silent --show-error http://127.0.0.1:8000/dashboard/api/inference
tail -n 20 artifacts/llm_telemetry.jsonl
tail -n 20 artifacts/inference_shadow.jsonl
```

Im Shadow bleibt der direkte bisherige Provider autoritativ. `role=shadow`,
`authoritative=current` und `influences_execution=false` müssen sichtbar sein. Signal
Consensus nutzt die Gateway-Route ausschließlich als nicht-autoritativen Vergleich.

## 5. Metriken und Graduation

Mindestens ein repräsentatives Shadow-Fenster sammeln; bei der gemessenen Last sind sieben
Tage der bevorzugte erste Reviewpunkt. Auswerten:

- Schema Success und Failure Rate nach Rolle/Route,
- p50/p95 gegenüber 6–7 s / ca. 20,9 s Baseline,
- Token, bekannte/unklare Kosten, Kosten/Call und /1000 Calls,
- Retry-/Fallbackrate und Circuit-Öffnungen,
- Direction-, Confidence-, Priority- und Critical-Field-Divergenz,
- Consensus-Abweichung ohne Änderung des autoritativen Ergebnisses.

Die konkreten Mindestschwellen stehen in ADR 0017. Fehlende reale Shadow-Evidenz ist
`NOT_PROVEN` und blockiert die Empfehlung für `primary`.

## 6. Primary (nur nach Operator-Freigabe)

```text
KAI_INFERENCE_ENABLED=true
KAI_INFERENCE_MODE=primary
```

Danach `kai-server.service` neu starten, Status, Journal und die ersten Calls beobachten.
OpenAI bleibt als konfigurierter Last-Resort-Alias und direkter Legacy-Fallback verfügbar.
Keine Trading-/Approval-/Risk-Flags zusammen mit diesem Wechsel ändern.

## 7. Sofort-Rollback

Eine Konfigurationsentscheidung genügt:

```text
KAI_INFERENCE_MODE=off
```

```bash
sudo systemctl restart kai-server.service
curl --fail --silent --show-error http://127.0.0.1:8000/dashboard/api/inference
systemctl status kai-server.service --no-pager
```

Erwartet: `mode=off`; neue operative Calls laufen wieder über den direkten Altpfad. Kein
DB-Rollback und kein Stop des Gateways ist nötig. Optional erst nach bestätigtem KAI-Health:
`sudo systemctl stop kai-litellm.service`.

## 8. Troubleshooting

- Server läuft, Gateway down: in `off` normal; in `shadow` bleibt der Altpfad autoritativ;
  in `primary` greifen begrenzte Versuche und direkter Fallback. Journal zuerst prüfen.
- 401/403: Master-Key-Gleichheit prüfen; Auth-Fehler werden absichtlich nicht retried oder
  auf andere Gateway-Aliase gefallbackt.
- 429/5xx/Timeout: Telemetrie auf Attempts, Backoff, Fallback und Circuit prüfen.
- Schema-Verletzung: Payload wird KAI-seitig verworfen; Modell-/Alias-Konfiguration prüfen.
- Kosten `null`: Response lieferte keine Kosten und es existiert keine passende explizite
  `KAI_INFERENCE_MODEL_PRICES_USD`-Metadatenzeile. Nicht als `$0` interpretieren.
- Budget blockiert: Daily/Monthly sind Kalenderfenster; bei `critical` ist unbekannter
  Requestpreis mit aktivem Hard-Limit absichtlich fail-closed.
