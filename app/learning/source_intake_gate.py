"""Fail-closed intake gate for autonomously discovered source candidates.

Phase 3 foundation (PR5a). A discovered candidate must pass EVERY gate before it
may enter ``probation``; any failure rejects it with a recorded reason. This module
makes NO content fetch — it validates a candidate URL (Rail 1 SSRF, which performs
a DNS resolution only), de-duplicates against known sources, and classifies the
declared access mode (Rail 2). Actual probing happens later and only inside the
``app/exploration`` sandbox.

Rails honoured (KAI §5 / ADR-0006):
- **Rail 1 (SSRF):** ``validate_url`` runs before anything else; a blocked URL is
  rejected, never fetched.
- **Rail 2 (ToS/Access):** a candidate is NEVER onboarded as ``active`` — the best
  outcome is ``probation``. Login/paywall/captcha access is rejected outright;
  scrape/website access is accepted but flagged ``sandbox_only``.
- **Fail-closed:** an unknown/unspecified access mode is REJECTED, not waved
  through — the default is "no", so a missing classification cannot leak a source
  into the pipeline.

The gate is a pure function (the SSRF validator is injectable) so it is fully
deterministic and offline-testable — no live network in the unit tests.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from app.core.enums import SourceStatus, SourceType
from app.core.errors import SecurityError
from app.security.ssrf import validate_url


class CandidateAccess(StrEnum):
    """Declared access mode of a discovered candidate (drives the Rail-2 verdict)."""

    API = "api"  # documented API → accept (not sandbox)
    RSS = "rss"  # syndication feed → accept (not sandbox)
    WEBSITE = "website"  # public readable page → accept, sandbox-only (HTML parse)
    SCRAPE = "scrape"  # requires scraping → accept, sandbox-only
    LOGIN = "login"  # requires auth → reject
    PAYWALL = "paywall"  # paid content → reject
    CAPTCHA = "captcha"  # bot-blocked → reject
    UNKNOWN = "unknown"  # unspecified → reject (fail-closed)


# Access modes that may onboard (into probation), split by sandbox requirement.
_ACCEPT_DIRECT: frozenset[CandidateAccess] = frozenset({CandidateAccess.API, CandidateAccess.RSS})
_ACCEPT_SANDBOX: frozenset[CandidateAccess] = frozenset(
    {CandidateAccess.WEBSITE, CandidateAccess.SCRAPE}
)
_REJECT_ACCESS: frozenset[CandidateAccess] = frozenset(
    {CandidateAccess.LOGIN, CandidateAccess.PAYWALL, CandidateAccess.CAPTCHA}
)


@dataclass(frozen=True)
class SourceCandidate:
    """A proposed source awaiting intake (e.g. from the discovery scheduler)."""

    url: str
    access: CandidateAccess
    source_type: SourceType
    provider: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class IntakeDecision:
    """Verdict of the intake gate for one candidate."""

    accepted: bool
    reason: str
    status: SourceStatus | None  # PROBATION when accepted, else None
    sandbox_only: bool
    normalized_url: str | None


def normalize_url(url: str) -> str:
    """Canonicalise a URL for de-duplication.

    Lower-cases scheme + host, drops the fragment, and strips a trailing slash on
    the path. Conservative on purpose — query strings are preserved (they can be
    load-bearing for APIs). Returns ``""`` for an unparseable/empty URL.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def evaluate_source_intake(
    candidate: SourceCandidate,
    *,
    existing_normalized_urls: Collection[str],
    url_validator: Callable[[str], None] = validate_url,
) -> IntakeDecision:
    """Decide whether a candidate may enter probation. Fail-closed at every step.

    Order matters: SSRF (Rail 1) first so a blocked host is never even normalised
    for comparison; then dedup; then access classification (Rail 2). The only
    accepted ``status`` is ``probation`` — this function can never return ``active``.
    """
    # Rail 1 — SSRF. A blocked URL is rejected before any other consideration.
    try:
        url_validator(candidate.url)
    except SecurityError as exc:
        return IntakeDecision(False, f"ssrf_blocked: {exc}", None, False, None)

    normalized = normalize_url(candidate.url)
    if not normalized:
        return IntakeDecision(False, "empty_or_malformed_url", None, False, None)

    # De-duplication against known sources (compare on the same normal form).
    known = {normalize_url(u) for u in existing_normalized_urls}
    if normalized in known:
        return IntakeDecision(False, "duplicate", None, False, normalized)

    # Rail 2 — access classification.
    access = candidate.access
    if access in _REJECT_ACCESS:
        return IntakeDecision(False, f"access_rejected: {access.value}", None, False, normalized)
    if access in _ACCEPT_DIRECT:
        return IntakeDecision(
            True, f"accepted: {access.value}", SourceStatus.PROBATION, False, normalized
        )
    if access in _ACCEPT_SANDBOX:
        return IntakeDecision(
            True, f"accepted_sandbox: {access.value}", SourceStatus.PROBATION, True, normalized
        )
    # Fail-closed: UNKNOWN or any future/unmapped access mode is rejected.
    return IntakeDecision(
        False, f"access_unknown_fail_closed: {access.value}", None, False, normalized
    )


__all__ = [
    "CandidateAccess",
    "IntakeDecision",
    "SourceCandidate",
    "evaluate_source_intake",
    "normalize_url",
]
