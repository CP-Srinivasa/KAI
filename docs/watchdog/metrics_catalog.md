# Metrics Catalog

STAB-03 erfasst nur Messwerte im `kai-server`; es definiert keine Schwellen und
keine Alarme.

| Metrik | Bedeutung | Fenster |
|---|---|---|
| `kai_event_loop_lag_seconds{window,quantile}` | Event-Loop-Lag als Überschuss über `asyncio.sleep(interval_s)`; Quantile `0.5`, `0.95`, `max`. | `60s`, `3600s` |
| `kai_event_loop_lag_samples_total{window}` | Anzahl der Lag-Samples im jeweiligen Fenster. | `60s`, `3600s` |
| `kai_http_request_duration_seconds{route,quantile}` | Request-Dauer je FastAPI-Route-Template, nie rohe Pfade mit Parameterwerten; Quantile `0.5`, `0.95`, `max`. | Sliding Window 10 min |
| `kai_http_requests_window_total{route}` | Anzahl der im Sliding Window gehaltenen Requests je Route-Template. | Sliding Window 10 min |
| `kai_process_uptime_seconds` | Laufzeit des aktuellen API-Prozesses. | Seit Prozessstart |
| `kai_process_start_time_seconds` | Unix-Zeit des API-Prozessstarts. | Fixer Startzeitpunkt |

Der JSONL-Rollup des Event-Loop-Lag-Samplers liegt unter
`artifacts/observability/event_loop_lag.jsonl`; bei mehr als 2000 Zeilen wird er
auf die letzten 1440 Zeilen gekürzt.

Schwellen/Alarme folgen erst nach 7 Tagen Messung (STAB-08).

**Zugriff:** `/metrics` ist lokal ohne Token lesbar (F-002-Muster wie die Dashboard-Reads, `reason=metrics_local`); Verkehr über den Cloudflare-Tunnel (`Cf-Ray`) bleibt hinter CF-Access/Bearer. Auf der Pi: `curl -s http://127.0.0.1:8000/metrics`.
