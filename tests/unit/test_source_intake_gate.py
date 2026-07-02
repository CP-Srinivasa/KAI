"""Unit tests for the fail-closed source intake gate (Phase 3 foundation, PR5a).

The gate is the chokepoint where an autonomously discovered candidate may enter
probation. These tests pin the security-load-bearing behaviour:
- SSRF (Rail 1) is checked first and a block rejects;
- de-duplication against known sources;
- access classification (Rail 2): api/rss accept, scrape/website sandbox-only,
  login/paywall/captcha reject, unknown FAIL-CLOSED reject;
- the gate can NEVER return ``active`` — the best outcome is probation.

The SSRF validator is injected so the tests are fully offline + deterministic.
"""

from __future__ import annotations

import pytest

from app.core.enums import SourceStatus, SourceType
from app.core.errors import SecurityError
from app.learning.source_intake_gate import (
    CandidateAccess,
    SourceCandidate,
    evaluate_source_intake,
    normalize_url,
)


def _ok_validator(_url: str) -> None:
    """Stand-in SSRF validator that always passes (no network)."""
    return None


def _blocking_validator(_url: str) -> None:
    raise SecurityError("blocked for test")


def _candidate(
    url: str = "https://feeds.example.com/crypto.xml",
    access: CandidateAccess = CandidateAccess.RSS,
    source_type: SourceType = SourceType.RSS_FEED,
) -> SourceCandidate:
    return SourceCandidate(url=url, access=access, source_type=source_type)


# ── normalize_url ──────────────────────────────────────────────────────────


def test_normalize_lowercases_scheme_host_strips_trailing_slash_and_fragment() -> None:
    assert (
        normalize_url("HTTPS://Feeds.Example.COM/Crypto/#section")
        == "https://feeds.example.com/Crypto"
    )


def test_normalize_preserves_query() -> None:
    assert normalize_url("https://api.x.com/v1?key=a") == "https://api.x.com/v1?key=a"


def test_normalize_empty_and_malformed_return_empty() -> None:
    assert normalize_url("") == ""
    assert normalize_url("not-a-url") == ""
    assert normalize_url("mailto:x@y.com") == ""  # no netloc


# ── Rail 1: SSRF ───────────────────────────────────────────────────────────


def test_ssrf_block_rejects_before_anything_else() -> None:
    decision = evaluate_source_intake(
        _candidate(),
        existing_normalized_urls=[],
        url_validator=_blocking_validator,
    )
    assert decision.accepted is False
    assert decision.status is None
    assert decision.reason.startswith("ssrf_blocked")


# ── dedup ──────────────────────────────────────────────────────────────────


def test_duplicate_candidate_is_rejected() -> None:
    decision = evaluate_source_intake(
        _candidate(url="https://feeds.example.com/crypto.xml/"),  # trailing slash variant
        existing_normalized_urls=["HTTPS://feeds.example.com/crypto.xml"],  # case variant
        url_validator=_ok_validator,
    )
    assert decision.accepted is False
    assert decision.reason == "duplicate"


# ── Rail 2: access classification ──────────────────────────────────────────


@pytest.mark.parametrize("access", [CandidateAccess.API, CandidateAccess.RSS])
def test_api_and_rss_accepted_into_probation_not_sandbox(access: CandidateAccess) -> None:
    decision = evaluate_source_intake(
        _candidate(access=access),
        existing_normalized_urls=[],
        url_validator=_ok_validator,
    )
    assert decision.accepted is True
    assert decision.status is SourceStatus.PROBATION
    assert decision.sandbox_only is False


@pytest.mark.parametrize("access", [CandidateAccess.WEBSITE, CandidateAccess.SCRAPE])
def test_website_and_scrape_accepted_sandbox_only(access: CandidateAccess) -> None:
    decision = evaluate_source_intake(
        _candidate(access=access, source_type=SourceType.WEBSITE),
        existing_normalized_urls=[],
        url_validator=_ok_validator,
    )
    assert decision.accepted is True
    assert decision.status is SourceStatus.PROBATION
    assert decision.sandbox_only is True


@pytest.mark.parametrize(
    "access", [CandidateAccess.LOGIN, CandidateAccess.PAYWALL, CandidateAccess.CAPTCHA]
)
def test_login_paywall_captcha_rejected(access: CandidateAccess) -> None:
    decision = evaluate_source_intake(
        _candidate(access=access),
        existing_normalized_urls=[],
        url_validator=_ok_validator,
    )
    assert decision.accepted is False
    assert decision.status is None
    assert decision.reason.startswith("access_rejected")


def test_unknown_access_is_fail_closed() -> None:
    decision = evaluate_source_intake(
        _candidate(access=CandidateAccess.UNKNOWN),
        existing_normalized_urls=[],
        url_validator=_ok_validator,
    )
    assert decision.accepted is False
    assert decision.status is None
    assert "fail_closed" in decision.reason


def test_gate_never_returns_active_for_any_access() -> None:
    """Invariant: the gate can never onboard straight to active."""
    for access in CandidateAccess:
        decision = evaluate_source_intake(
            _candidate(access=access),
            existing_normalized_urls=[],
            url_validator=_ok_validator,
        )
        assert decision.status is not SourceStatus.ACTIVE
        if decision.accepted:
            assert decision.status is SourceStatus.PROBATION
