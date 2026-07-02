# Sprint 22: Canonical Monitoring & Readiness Stack Contract

## 1. Core Principle (I-123)
All additions to the Operational Alerts and Readiness modules MUST remain **strictly observational**.
- **No Execution Triggers**: Alerts are declarative metrics. They never trigger signal consumption or automated routing swaps.
- **No Database Mutation**: Operational checks derive exclusively from local artifact JSON/JSONL outputs (such as `RouteProfileReport`, `DistributionClassificationReport`, and `HandoffCollectorSummary`).
- **No Auto-Remediation (I-128)**: System anomalies raise `SEVERITY_WARNING` or `SEVERITY_CRITICAL` alerts to the operator. The bot will not automatically mutate components to self-heal.

## 2. Checkers and the Data Tier
The monitoring stack now encapsulates 5 core observation points within `app.research.operational_alerts`:

1. **Route Health (`CATEGORY_ROUTE_HEALTH`)**
   Checks if the active Route Profile analyzed > 0 total documents.
2. **Provider Health (`CATEGORY_PROVIDER_HEALTH`)**
   Evaluates active `TierProfile` paths. Alerts if a configured tier processes exactly 0 documents (indicating a dead API or misconfiguration).
3. **Signal Quality (`CATEGORY_SIGNAL_QUALITY`)**
   Evaluates `spam` vs `document_count` ratios per tier, warning automatically if spam exceeds 90%.
4. **Collector Anomalies (`CATEGORY_COLLECTOR_ANOMALY`)**
   Checks `HandoffCollectorSummaryReport` for orphaned consumer acknowledgements and critically high pending backlogs.
5. **Distribution Drift (`CATEGORY_DISTRIBUTION_DRIFT`)**
   Inspects `DistributionClassificationReport` audit outputs. Alerts if Shadow companion path generated signals but Primary produced `0` (high risk of drift), or outputs the volume of baseline Control audits.

## 3. Interfaces
- **MCP Server**: Exposes `get_provider_health` and `get_distribution_drift` alongside `get_operational_readiness_summary`.
- **CLI Framework**: Operators invoke `research provider-health --route-profile <path>` and `research drift-summary --distribution <path>`.

*These commands and tools permanently replace iterative or ad-hoc analysis shims.*
