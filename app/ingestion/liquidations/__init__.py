"""Liquidation ingestion adapters (#316 Data Foundation).

Provider-specific normalizers that map a raw wire payload into the canonical
``app.market_data.liquidation_event.LiquidationEvent``. Read-only: ingestion
only observes and records; it never touches trading/risk/execution.
"""
