"""Edge-discovery research engine.

Composes the causal feature matrix (``app.analysis.features``) and forward-return
labels into a disciplined hypothesis search: rule -> net-bps trades -> walk-forward
evaluation -> multiple-testing-controlled accept/reject. Built to FIND edge
honestly (and to report "no edge" honestly), not to manufacture it.
"""
