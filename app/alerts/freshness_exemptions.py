"""Freshness-Ausnahmen des Health-Checks: welche Veralterung KEIN Probe-Defekt ist.

Herausgeloest aus ``health_check.py`` (God-File-Ratchet, null Headroom; C5 17.09.).
Die Schwellentabelle ``_FRESHNESS_PER_FILE_MIN`` bleibt bewusst dort — das
Stream-Consumer-Gate (scripts/stream_consumer_ratchet.py) liest sie per AST.
"""

from __future__ import annotations

#: C5 (Sprint S-0917): Stroeme, deren Stille ein SYSTEMBEFUND ist (LLM-Kette,
#: Request-Middleware) — Befund ja, aber kein ``stale``: --exit-on-stale darf an
#: ihnen nicht den ganzen Report verschlucken (Lehre alert_audit, 18.08.).
SYSTEM_STREAM_COMPONENTS: frozenset[str] = frozenset({"llm_telemetry", "api_request_audit"})

_INGRESS_COMPONENTS: frozenset[str] = frozenset(
    {"tradingview_ingress", "liquidation_ingress", "document_ingest"}
)

# Komponenten, deren Veralterung NICHTS ueber die Verlaesslichkeit der Probe
# aussagt und darum `data_sources_stale` (und damit --exit-on-stale) nicht
# ausloesen darf. Zwei Faelle, ein Prinzip:
#   * Eingangsstroeme  — die Quelle schweigt (Systembefund).
#   * Ereignisgetriebene Ausgaenge — der Kanal hat nichts zu sagen gehabt.
# `alert_audit` gehoerte bis 2026-08-18 faelschlich in die Abbruch-Kategorie.
# Folge (Pi-Journal): 66 Abbrueche in 14 Tagen mit "stale data", WAEHREND im
# selben Lauf cycles=1111..1117 standen und `trading_loop_audit` (30-min-
# Schwelle, taktgetrieben) still blieb — die Probe las beweisbar Live-Daten.
# Jeder Abbruch verschluckte den ganzen Report und loeste 5-Minuten-Watchdog-
# Spam aus: der Waechter verstummte genau dann, wenn er melden sollte.
#
# Was den Abbruch WEITERHIN ausloest, ist der taktgetriebene Beweis: schreibt
# `trading_loop_audit` (~1200 Zyklen/Tag) nicht mehr, liest die Probe wirklich
# gespiegelte/veraltete Artefakte. Das ist die Frage, die das Flag beantwortet.
_PROBE_RELIABILITY_EXEMPT: frozenset[str] = (
    _INGRESS_COMPONENTS | frozenset({"alerts"}) | SYSTEM_STREAM_COMPONENTS
)
