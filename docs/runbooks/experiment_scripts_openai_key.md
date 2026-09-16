# Experiment-Skripte: eigener OpenAI-Key

## Warum

Holdouts, A/B-Laeufe und Messungen rufen OpenAI ausserhalb des Gateways auf.
Sie erscheinen weder in `llm_telemetry.jsonl` noch im Tagesbudget. Ueber den
Produktiv-Key sind sie auch auf der OpenAI-Rechnung nicht vom Betrieb zu
trennen: Am 09.09. standen 310 gpt-4o- und 1.406 gpt-4o-mini-Aufrufe
(~2,2 USD) auf der Rechnung, in der Telemetrie nur 51 Versuche
(Abgleich 16.09.).

## Einrichtung (Operator, einmalig)

1. OpenAI-Plattform → API keys → neuer Key, Name `kai-scripts`
   (gleiches Projekt reicht; der Export `completions_usage_*.csv` trennt nach
   `api_key_id`).
2. Auf dem Pi in `/home/ubuntu/ai_analyst_trading_bot/.env` eintragen:
   `OPENAI_API_KEY_SCRIPTS=<key>` — Laenge danach pruefen, eingefuegte Keys
   waren schon einmal verdoppelt.
3. Kein Dienst-Neustart noetig: Der Server liest diese Variable nicht.

## Regel fuer Skripte

```python
from app.core.script_credentials import script_openai_api_key

provider = OpenAIAnalysisProvider(api_key=script_openai_api_key(), ...)
```

Nie `settings.providers.openai_api_key` in einem Skript. Fehlt der Skript-Key
oder ist er identisch mit `OPENAI_API_KEY`, bricht `script_openai_api_key()` ab
— absichtlich ohne Rueckfall auf den Produktiv-Key.

## Abgleich

Im OpenAI-Export `completions_usage_*.csv` die Zeilen nach `api_key_id`
trennen: Produktiv-Key ↔ `llm_telemetry.jsonl` (nur `chain_position >= 0`),
Skript-Key = Experimentkosten.
