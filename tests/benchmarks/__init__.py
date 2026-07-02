"""KAI Performance-Benchmarks.

ADR 0003 Pflichtmetriken (DuckDB-Pivot):
- RAM < 2 GB nach 30 Tagen Daten
- Query-Latency < 50 ms p95
- CPU-Idle-Impact < 5 %
- Crash-Safe Recovery

Diese Benchmarks sind harte Fail-Kriterien — keine Skip-marker bei
langsamer CI. Wenn die Pflichtmetrik nicht hält, ist die Implementation
nicht ready für Cutover.
"""
