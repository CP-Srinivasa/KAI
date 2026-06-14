"""OKX exchange-announcement source integration.

Polls OKX's official public announcements API (v5) — listings, delistings,
maintenance — and emits them as CanonicalDocuments for the analysis pipeline.
Exchange listings are the highest-impact, most clearly directional event class
(source-scout 2026-06-14); OKX is the surviving free exchange source after the
Coinbase RSS (403) and DefiLlama raises (402) endpoints became unavailable.
"""
