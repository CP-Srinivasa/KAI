"""Unit tests for the source-discovery scheduler (Phase 3, observe/decide/audit).

Pins the dry-by-default loop: proposals are gated fail-closed, the rotation pool is
read from the ranking, graduation is decided, and PROPOSED actions are audited with
NO DB mutation. The SSRF validator is injected so the tests are offline/deterministic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.source_discovery_scheduler import (
    gate_proposals,
    read_proposals,
    rotation_pool_from_ranking,
    run_once,
)

from app.core.enums import SourceType
from app.core.errors import SecurityError
from app.learning.source_intake_gate import CandidateAccess, SourceCandidate

_NOW = datetime(2026, 6, 24, 5, 0, tzinfo=UTC)


def _ok(_url: str) -> None:
    return None


def _block(_url: str) -> None:
    raise SecurityError("blocked")


# ── read_proposals ─────────────────────────────────────────────────────────


def test_read_proposals_tolerant(tmp_path: Path) -> None:
    p = tmp_path / "source_proposals.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps(
                    {"url": "https://a.com/feed", "access": "rss", "source_type": "rss_feed"}
                ),
                "{ broken json",
                json.dumps({"access": "rss"}),  # no url → skipped
                json.dumps({"url": "https://b.com", "access": "weird"}),  # bad access → UNKNOWN
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = read_proposals(p)
    assert [c.url for c in out] == ["https://a.com/feed", "https://b.com"]
    assert out[1].access is CandidateAccess.UNKNOWN  # fail-closed mapping


def test_read_proposals_missing_file(tmp_path: Path) -> None:
    assert read_proposals(tmp_path / "nope.jsonl") == []


# ── gate_proposals ─────────────────────────────────────────────────────────


def test_gate_accepts_rss_rejects_login_and_dedups_within_batch() -> None:
    proposals = [
        SourceCandidate("https://a.com/feed", CandidateAccess.RSS, SourceType.RSS_FEED),
        SourceCandidate("https://a.com/feed", CandidateAccess.RSS, SourceType.RSS_FEED),  # dup
        SourceCandidate("https://paid.com", CandidateAccess.PAYWALL, SourceType.WEBSITE),
    ]
    accepted, rejected = gate_proposals(proposals, set(), url_validator=_ok)
    assert [c.url for c, _ in accepted] == ["https://a.com/feed"]
    reasons = dict(rejected)
    assert reasons["https://a.com/feed"] == "duplicate"
    assert reasons["https://paid.com"].startswith("access_rejected")


def test_gate_ssrf_block_rejects() -> None:
    proposals = [SourceCandidate("https://a.com/feed", CandidateAccess.RSS, SourceType.RSS_FEED)]
    accepted, rejected = gate_proposals(proposals, set(), url_validator=_block)
    assert accepted == []
    assert rejected[0][1].startswith("ssrf_blocked")


# ── rotation_pool_from_ranking ─────────────────────────────────────────────


def test_rotation_pool_only_flagged_weakest_first() -> None:
    ranking = {
        "ranked": [
            {"source_name": "keep", "rotation_flagged": False, "wilson_lower_95": 0.6},
            {"source_name": "rotA", "rotation_flagged": True, "wilson_lower_95": 0.30},
            {"source_name": "rotB", "rotation_flagged": True, "wilson_lower_95": 0.10},
        ]
    }
    pool = rotation_pool_from_ranking(ranking)
    assert [r.source for r in pool] == ["rotA", "rotB"]  # order preserved as-is
    assert {r.source for r in pool} == {"rotA", "rotB"}
    assert all(r.source != "keep" for r in pool)


# ── run_once (dry, no DB) ──────────────────────────────────────────────────


def test_run_once_dry_audits_proposals_and_writes_summary(tmp_path: Path) -> None:
    (tmp_path / "monitor").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "monitor" / "source_proposals.jsonl").write_text(
        json.dumps({"url": "https://good.com/rss", "access": "rss", "source_type": "rss_feed"})
        + "\n"
        + json.dumps({"url": "https://login.com", "access": "login", "source_type": "website"})
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "monitor" / "source_ranking.json").write_text(
        json.dumps(
            {"ranked": [{"source_name": "weak", "rotation_flagged": True, "wilson_lower_95": 0.1}]}
        ),
        encoding="utf-8",
    )
    runs_path = tmp_path / "monitor" / "source_discovery_runs.jsonl"
    summary = run_once(
        proposals_path=tmp_path / "monitor" / "source_proposals.jsonl",
        ranking_path=tmp_path / "monitor" / "source_ranking.json",
        audit_dir=tmp_path / "artifacts",
        runs_path=runs_path,
        enabled=False,
        now=_NOW,
        url_validator=_ok,
    )
    assert summary["mode"] == "dry"
    assert summary["proposals_seen"] == 2
    assert summary["accepted"] == 1  # rss accepted, login rejected
    assert summary["rejected"] == 1
    assert summary["rotation_pool"] == 1
    # Graduation is honestly inert (no probation evidence yet) → no swaps.
    assert summary["graduation_swaps"] == 0
    # Run summary persisted.
    assert runs_path.exists()
    # The accepted proposal is audited as a PROPOSED probation (executed=False).
    audit = (tmp_path / "artifacts" / "source_lifecycle_audit.jsonl").read_text(encoding="utf-8")
    assert "discovery_intake_dry" in audit
    assert '"executed": false' in audit
    assert "to_status" in audit and "probation" in audit


def test_run_once_no_inputs_is_empty_observe(tmp_path: Path) -> None:
    (tmp_path / "monitor").mkdir()
    (tmp_path / "artifacts").mkdir()
    summary = run_once(
        proposals_path=tmp_path / "monitor" / "source_proposals.jsonl",
        ranking_path=tmp_path / "monitor" / "source_ranking.json",
        audit_dir=tmp_path / "artifacts",
        runs_path=tmp_path / "monitor" / "source_discovery_runs.jsonl",
        enabled=False,
        now=_NOW,
        url_validator=_ok,
    )
    assert summary["proposals_seen"] == 0
    assert summary["accepted"] == 0
    assert summary["graduation_swaps"] == 0
