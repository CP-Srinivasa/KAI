"""Signal-source taxonomy — the ONE coarse source bucket for fills/closes/shadow.

Extracted from ``trading_loop`` (2026-06-29) so the bucket logic is a small, pure,
directly-tested seam instead of an inline branch buried in the god-file ``run_cycle``.
Reports (edge_report ``by_source``, canonical-edge source filter, D-227/hit-rate)
all key off the value this module returns, so a forgotten branch silently
mis-attributes a whole cohort — which is exactly how ``momentum_universe`` paper
fills were counted as ``autonomous_generator`` until this was generalised.
"""

from __future__ import annotations

# NEO-P-002 (Weg B): ONE source taxonomy shared by the fill path (#132) and the
# shadow path (#137). #137 used a second, divergent mapping in the shadow path
# (event_type "control_plane" -> canary_probe, else "autonomous_loop"). That
# split the fill bucket ("autonomous_generator") from the shadow bucket
# ("autonomous_loop") for the very same real generator. This helper unifies BOTH
# on the document_id-based detection so a single bucket name joins across fill,
# close and shadow aggregations. "autonomous_loop" is never produced as a NEW
# value again — it survives only as a read-back default for the 644 legacy
# ledger rows (see legacy_counts in build_shadow_report).
SOURCE_CANARY_PROBE = "canary_probe"
SOURCE_AUTONOMOUS_GENERATOR = "autonomous_generator"
SOURCE_UNKNOWN = ""
# Goal 2026-06-10: hard source tag for decoupled real-analysis paper fills.
# Excluded from edge/D-227/hit-rate headlines by default (B-002), like canary.
SOURCE_REAL_ANALYSIS = "real_analysis"
SOURCE_TECHNICAL_PAPER = "technical_paper"


def derive_autonomous_signal_source(document_id: str | None) -> str:
    """Map an originating document_id to its coarse autonomous source bucket.

    Pure / no side effects. ``loop_control_*`` doc-ids are the hardcoded
    control-plane canary probes; any other non-empty doc-id is the real
    generator; ``""``/None means the origin is genuinely unknown — never
    silently relabelled as a real signal (CLAUDE.md: no silent assumptions).
    """
    doc_id = document_id or ""
    if doc_id.startswith("loop_control_"):
        return SOURCE_CANARY_PROBE
    if doc_id:
        return SOURCE_AUTONOMOUS_GENERATOR
    return SOURCE_UNKNOWN


def resolve_signal_source(
    attribution_doc_id: str,
    *,
    real_analysis_feed: bool,
    technical_paper_feed: bool,
    analysis_source: str | None,
) -> str:
    """Return the coarse signal_source bucket for one fill/close.

    Precedence (first match wins):
      1. ``real_analysis_feed``    → ``real_analysis``    (B-002 hard attribution)
      2. ``technical_paper_feed``  → ``technical_paper``  (B-002 hard attribution)
      3. any explicit ``analysis_source`` tag → that tag verbatim. This is the
         GENERIC cohort branch (2026-06-29): a feeder that tags its cycles (e.g.
         ``momentum_universe``) gets its OWN bucket so edge_report splits it out
         and the canonical-edge source filter excludes it — instead of falling
         through to the ``autonomous_generator`` default below. The autonomous
         loop passes ``analysis_source=None`` and is byte-unchanged; this closes
         the recurring "the taxonomy whitelist forgot the new cohort" bug class.
      4. otherwise → the document_id-derived autonomous bucket.
    """
    if real_analysis_feed:
        return SOURCE_REAL_ANALYSIS
    if technical_paper_feed:
        return SOURCE_TECHNICAL_PAPER
    if analysis_source:
        return analysis_source
    return derive_autonomous_signal_source(attribution_doc_id)


__all__ = [
    "SOURCE_AUTONOMOUS_GENERATOR",
    "SOURCE_CANARY_PROBE",
    "SOURCE_REAL_ANALYSIS",
    "SOURCE_TECHNICAL_PAPER",
    "SOURCE_UNKNOWN",
    "derive_autonomous_signal_source",
    "resolve_signal_source",
]
