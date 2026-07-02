"""Capital segmentation & reserve policy (ADR 0013, shadow-only, inert).

Pure, read-only accounting for the four capital buckets (operating/reserve/
long_term/experiment) and the reserve/profit-split recommendation. No module here
moves capital; allocation/transfer is gated at the call site (HOTP +
edge-validation-gate). Not wired into any consumer yet.
"""
