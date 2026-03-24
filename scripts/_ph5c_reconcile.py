"""PH5C Governance Reconciliation — single-pass canonical freeze.

Canonical state to enforce:
  current_phase:      PHASE 5 (active) -- Signal Reliability & Trust
  current_sprint:     PH5C_FILTER_BEFORE_LLM_BASELINE (active D-95, §85)
  next_required_step: PH5C_EXECUTION
  phase_5_status:     active -- PH5A closed (D-91); PH5B closed (D-94); PH5C active (D-95)
  baseline:           1619 passed, ruff clean, mypy 0 errors

D-number canonical sequence:
  D-91: PH5A results-review complete
  D-92: PH5B opened
  D-93: PH5B execution complete
  D-94: PH5B closed (merge two D-94 entries)
  D-95: PH5C opened
"""
from __future__ import annotations

import re
from pathlib import Path


def read(p: str) -> str:
    with open(p, "rb") as f:
        return f.read().decode("latin-1")


def write(p: str, t: str) -> None:
    with open(p, "w", encoding="utf-8") as f:
        f.write(t)


# ── Canonical values ─────────────────────────────────────────────────────────
SPRINT = "PH5C_FILTER_BEFORE_LLM_BASELINE (active D-95, §85)"
NEXT_STEP = "PH5C_EXECUTION"
PHASE5_STATUS = "active -- PH5A closed (D-91); PH5B closed (D-94); PH5C active (D-95)"
BASELINE = "1619 passed, ruff clean, mypy 0 errors"
CURRENT_PHASE = "PHASE 5 (active) -- Signal Reliability & Trust"


def normalise(t: str) -> str:
    """Fix double-encoded UTF-8 and replacement char artifacts."""
    t = t.replace("\xc2\xa7", "§")       # double-encoded §
    t = t.replace("\xef\xbf\xbd", "§")   # UTF-8 replacement char
    t = t.replace("\xa7", "§")           # latin-1 §
    return t


def apply_canonical_fields(t: str) -> str:
    """Replace all known variants of the canonical fields with canonical values."""
    # current_sprint variants
    t = re.sub(
        r"current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE[^`]*`",
        f"current_sprint: `{SPRINT}`",
        t,
    )
    t = re.sub(
        r"\| current_sprint \| `[^`]*PH5C[^`]*` \|",
        f"| current_sprint | `{SPRINT}` |",
        t,
    )
    # next_required_step variants
    t = re.sub(
        r"next_required_step: `PH5C[^`]*`",
        f"next_required_step: `{NEXT_STEP}`",
        t,
    )
    t = re.sub(
        r"\| next_required_step \| `[^`]*PH5C[^`]*` \|",
        f"| next_required_step | `{NEXT_STEP}` |",
        t,
    )
    # phase_5_status / phase5_status variants
    t = re.sub(
        r"phase_5_status: `[^`]*PH5[^`]*`",
        f"phase_5_status: `{PHASE5_STATUS}`",
        t,
    )
    t = re.sub(
        r"\| phase5_status \| `[^`]*PH5[^`]*` \|",
        f"| phase5_status | `{PHASE5_STATUS}` |",
        t,
    )
    # baseline variants (only governance header lines, not inline artifact text)
    t = re.sub(
        r"(- baseline: `)(\d+ passed[^`]*)`",
        rf"\g<1>{BASELINE}`",
        t,
    )
    t = re.sub(
        r"(\| baseline \| `)([^`]*)(`)",
        rf"\g<1>{BASELINE}\g<3>",
        t,
    )
    return t


def fix_decision_log() -> None:
    t = normalise(read("DECISION_LOG.md"))

    # Remove duplicate D-94: keep only the canonical "PH5B review accepted" entry
    # The hook-generated D-94 says "PH5B ready to close; PH5C pre-LLM filter recommended"
    hook_d94_pattern = (
        r"### D-94 \(2026-03-24\): PH5B ready to close; PH5C pre-LLM filter recommended\n"
        r"(?:- [^\n]*\n)*"
    )
    t = re.sub(hook_d94_pattern, "", t)

    # Fix baseline references in header block of DECISION_LOG
    t = apply_canonical_fields(t)

    # Normalise the header block (first few lines) if present
    t = re.sub(
        r"(- current_sprint: `)([^`]*)(`)",
        rf"\g<1>{SPRINT}\g<3>",
        t,
        count=1,
    )

    write("DECISION_LOG.md", t)
    print("DECISION_LOG.md: duplicate D-94 removed, canonical fields set")


def fix_agents() -> None:
    t = normalise(read("AGENTS.md"))
    t = apply_canonical_fields(t)
    write("AGENTS.md", t)
    print("AGENTS.md: reconciled")


def fix_sprint_ledger() -> None:
    t = normalise(read("SPRINT_LEDGER.md"))
    t = apply_canonical_fields(t)
    # Fix PH5B table row D-number (hook used D-92, canonical is D-94)
    t = re.sub(
        r"\| PH5B \| 2026-03-24 \| closed \|[^\|]*\|",
        "| PH5B | 2026-03-24 | closed | Root cause: EMPTY_MANUAL 19/19 -- data quality gap, not model failure (D-94) |",
        t,
    )
    # Fix phase_5_status header
    t = re.sub(
        r"phase_5_status: `[^`]+`",
        f"phase_5_status: `{PHASE5_STATUS}`",
        t,
    )
    write("SPRINT_LEDGER.md", t)
    print("SPRINT_LEDGER.md: reconciled")


def fix_knowledge_base() -> None:
    t = normalise(read("KNOWLEDGE_BASE.md"))
    t = apply_canonical_fields(t)
    # Fix frontmatter line
    t = re.sub(
        r"> Stand: 2026-03-24 \|[^\n]+",
        f"> Stand: 2026-03-24 | {CURRENT_PHASE} | PH5C active (D-95, §85) | next_required_step: {NEXT_STEP} | baseline: {BASELINE}",
        t,
    )
    write("KNOWLEDGE_BASE.md", t)
    print("KNOWLEDGE_BASE.md: reconciled")


def fix_phase_plan() -> None:
    t = normalise(read("PHASE_PLAN.md"))
    t = apply_canonical_fields(t)
    write("PHASE_PLAN.md", t)
    print("PHASE_PLAN.md: reconciled")


def fix_risk_register() -> None:
    t = normalise(read("RISK_REGISTER.md"))
    t = apply_canonical_fields(t)
    write("RISK_REGISTER.md", t)
    print("RISK_REGISTER.md: reconciled")


def fix_tasklist() -> None:
    t = normalise(read("TASKLIST.md"))
    t = apply_canonical_fields(t)
    write("TASKLIST.md", t)
    print("TASKLIST.md: reconciled")


def fix_changelog() -> None:
    t = normalise(read("CHANGELOG.md"))
    # No sprint fields to replace; just normalise encoding
    write("CHANGELOG.md", t)
    print("CHANGELOG.md: encoding normalised")


def fix_contracts() -> None:
    t = normalise(read("docs/contracts.md"))
    # Fix §85 status line if it says frozen
    t = t.replace("§85 status: **frozen", "§85 status: **active")
    # Ensure §84 is closed
    t = re.sub(r"§84 status: \*\*(?!closed).*?\*\*", "§84 status: **closed (D-94, 2026-03-24)**", t)
    write("docs/contracts.md", t)
    print("docs/contracts.md: encoding + §85 status normalised")


def fix_intelligence_architecture() -> None:
    t = normalise(read("docs/intelligence_architecture.md"))
    # Update any stale phase header
    t = re.sub(
        r"PHASE 5.*?Signal Reliability.*?(active|ACTIVE)[^\n]*",
        f"{CURRENT_PHASE} | PH5C active (D-95, §85)",
        t,
    )
    write("docs/intelligence_architecture.md", t)
    print("docs/intelligence_architecture.md: normalised")


if __name__ == "__main__":
    print("=== PH5C Governance Reconciliation ===\n")
    fix_decision_log()
    fix_agents()
    fix_sprint_ledger()
    fix_knowledge_base()
    fix_phase_plan()
    fix_risk_register()
    fix_tasklist()
    fix_changelog()
    fix_contracts()
    fix_intelligence_architecture()
    print("\nReconciliation complete.")
