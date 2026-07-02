"""Disruptive focus-field taxonomy — the structural lens for thematic variation.

The asset universe (``app/trading/asset_universe.py``) already carries free-text
``sector`` and ``narrative``.  Those are useful but unbounded: they cannot be
aggregated, capped or reported on consistently because every curator can invent
a new string.  This module adds a *closed, canonical* taxonomy of the disruptive
focus fields the operator wants KAI to verify and harden first, and a
deterministic classifier that maps an asset's free-text dimensions onto exactly
one canonical field (or ``unknown``).

Design contract (KAI rules: honesty, modularity, no fabrication):
    * The taxonomy is a *closed set* of canonical IDs — stable, documented,
      aggregatable.  New fields are added here on purpose, not invented per asset.
    * Classification precedence is: explicit overlay ``focus_field`` (authoritative)
      → keyword inference from sector/narrative/tags → ``unknown``.  We never
      guess a field from a single weak token; a match needs a curated token.
    * ``unknown`` is a first-class, honest answer.  An asset nobody mapped is NOT
      silently bucketed into a field.

This is pure data + pure functions: no I/O, no market calls, deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

UNKNOWN = "unknown"


@dataclass(frozen=True)
class FocusField:
    """One canonical disruptive focus field."""

    field_id: str  # canonical, stable, kebab/snake — e.g. "ai", "gene_editing"
    label: str  # human label for reports/UI
    description: str
    # Curated match tokens (lowercase). A token matches if it is a substring of,
    # or equal to, any of the asset's sector/narrative/tag strings. Tokens are
    # deliberately specific to avoid false positives.
    tokens: frozenset[str] = field(default_factory=frozenset)


# ── Canonical taxonomy ───────────────────────────────────────────────────────
# Operator-defined disruptive focus fields. Order matters: it is the tie-break
# precedence when an asset matches more than one field (earlier wins). Keep the
# more specific fields above the broad ones (e.g. ai_crypto specifics before the
# broad blockchain bucket).
_FOCUS_FIELDS: tuple[FocusField, ...] = (
    FocusField(
        field_id="ai",
        label="Künstliche Intelligenz",
        description="AI/ML compute, models, AI-native crypto and AI infrastructure.",
        tokens=frozenset(
            {
                "ai",
                "artificial_intelligence",
                "ai_compute",
                "ai_crypto",
                "machine_learning",
                "ml",
                "gpu",
                "compute",
                "inference",
                "semiconductors",
                "ai_infra",
            }
        ),
    ),
    FocusField(
        field_id="dna_sequencing",
        label="DNA-Sequenzierung",
        description="Genomic sequencing platforms and instruments.",
        tokens=frozenset(
            {"dna_sequencing", "sequencing", "genomics", "genomic", "ngs", "long_read"}
        ),
    ),
    FocusField(
        field_id="gene_editing",
        label="Gen-Editing",
        description="CRISPR and gene-/base-editing therapeutics and platforms.",
        tokens=frozenset(
            {"gene_editing", "crispr", "base_editing", "gene_therapy", "genome_editing"}
        ),
    ),
    FocusField(
        field_id="multiomics",
        label="Multiomics",
        description="Proteomics, transcriptomics, single-cell and multi-omics platforms.",
        tokens=frozenset(
            {"multiomics", "proteomics", "transcriptomics", "single_cell", "spatial_omics", "omics"}
        ),
    ),
    FocusField(
        field_id="robotics",
        label="Robotik",
        description="Industrial, surgical, humanoid and autonomous robotics.",
        tokens=frozenset(
            {
                "robotics",
                "robot",
                "humanoid",
                "automation_robotics",
                "surgical_robotics",
                "autonomy",
            }
        ),
    ),
    FocusField(
        field_id="ev",
        label="Elektromobilität",
        description="Electric vehicles, drivetrains and charging.",
        tokens=frozenset({"ev", "electric_vehicle", "electric_vehicles", "charging", "drivetrain"}),
    ),
    FocusField(
        field_id="space",
        label="Raketen-/Satellitentechnik",
        description="Launch, satellites, earth observation and space infrastructure.",
        tokens=frozenset(
            {"space", "rocket", "rockets", "launch", "satellite", "satellites", "aerospace"}
        ),
    ),
    FocusField(
        field_id="communications",
        label="Kommunikation",
        description="Connectivity, telecom, networking and messaging infrastructure.",
        tokens=frozenset(
            {"communications", "telecom", "connectivity", "networking", "5g", "broadband"}
        ),
    ),
    FocusField(
        field_id="energy_storage",
        label="Energiespeicherung",
        description="Batteries, grid storage and energy-storage technology.",
        tokens=frozenset(
            {
                "energy_storage",
                "battery",
                "batteries",
                "grid_storage",
                "solid_state",
                "storage_tech",
            }
        ),
    ),
    FocusField(
        field_id="fintech",
        label="Fintech",
        description="Payments, brokerage, digital banking and financial infrastructure.",
        tokens=frozenset(
            {
                "fintech",
                "payments",
                "payments_fintech",
                "brokerage",
                "neobank",
                "digital_banking",
                "exchange_equity",
            }
        ),
    ),
    FocusField(
        field_id="additive_manufacturing",
        label="3D-Druck",
        description="Additive manufacturing and 3D printing.",
        tokens=frozenset(
            {"additive_manufacturing", "3d_printing", "3d_print", "additive", "printing_tech"}
        ),
    ),
    FocusField(
        field_id="blockchain",
        label="Blockchain & Krypto",
        description="L1/L2, DeFi, oracles, interoperability, RWA, store-of-value crypto.",
        tokens=frozenset(
            {
                "blockchain",
                "crypto",
                "smart_contract_l1",
                "l2_scaling",
                "oracle_infra",
                "interoperability",
                "store_of_value",
                "defi",
                "defi_infra",
                "rwa",
                "tokenization",
                "payments_crypto",
                "exchange_token",
                "meme",
            }
        ),
    ),
)

# Index by ID for fast validation/lookup.
_BY_ID: dict[str, FocusField] = {f.field_id: f for f in _FOCUS_FIELDS}

# Valid IDs plus the honest sentinel — what an overlay/AssetMeta may legitimately
# hold for ``focus_field``.
FOCUS_FIELD_IDS: frozenset[str] = frozenset(_BY_ID) | {UNKNOWN}


def all_focus_fields() -> tuple[FocusField, ...]:
    """The canonical taxonomy, in precedence order (read-only)."""
    return _FOCUS_FIELDS


def get_focus_field(field_id: str | None) -> FocusField | None:
    """Look up a canonical field by ID. ``None``/unknown → ``None``."""
    if not field_id:
        return None
    return _BY_ID.get(str(field_id).strip().lower())


def is_valid_focus_field(field_id: str | None) -> bool:
    """True if ``field_id`` is a canonical taxonomy ID (``unknown`` is not)."""
    return bool(field_id) and str(field_id).strip().lower() in _BY_ID


def _normalise(value: object) -> str:
    return str(value).strip().lower() if value is not None else ""


_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def _token_hit(haystacks: tuple[str, ...], tokens: frozenset[str]) -> bool:
    """Match curated tokens against an asset's free-text dimensions.

    A token matches when it equals a *whole word* of any haystack (after
    splitting on non-alphanumerics), or — only for longer compound tokens
    (len >= 5, e.g. ``smart_contract_l1``) — when it is a substring. The
    word-boundary rule prevents short tokens like ``ai`` from false-matching
    inside unrelated words (e.g. "drivetrain" contains the letters "ai").
    """
    words: set[str] = set()
    for hay in haystacks:
        if not hay:
            continue
        words.update(part for part in _WORD_SPLIT.split(hay) if part)
    for token in tokens:
        if token in words:
            return True
        if len(token) >= 5 and any(token in hay for hay in haystacks if hay):
            return True
    return False


def classify_focus_field(
    *,
    explicit: object = None,
    sector: object = None,
    narrative: object = None,
    tags: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Map an asset onto exactly one canonical focus field (or ``unknown``).

    Precedence:
      1. ``explicit`` — an operator-curated overlay value. If it is a valid
         canonical ID it wins outright; an invalid non-empty value degrades to
         inference (we do not honour a typo).
      2. Keyword inference from ``sector`` / ``narrative`` / ``tags`` against the
         curated token sets, in taxonomy precedence order.
      3. ``unknown`` — no curated signal. Never guessed.
    """
    explicit_norm = _normalise(explicit)
    if explicit_norm and explicit_norm in _BY_ID:
        return explicit_norm

    tag_strs = tuple(_normalise(t) for t in (tags or ()))
    haystacks = (_normalise(sector), _normalise(narrative), *tag_strs)
    haystacks = tuple(h for h in haystacks if h and h != UNKNOWN)
    if not haystacks:
        return UNKNOWN

    for focus in _FOCUS_FIELDS:
        if _token_hit(haystacks, focus.tokens):
            return focus.field_id
    return UNKNOWN


__all__ = [
    "UNKNOWN",
    "FOCUS_FIELD_IDS",
    "FocusField",
    "all_focus_fields",
    "classify_focus_field",
    "get_focus_field",
    "is_valid_focus_field",
]
