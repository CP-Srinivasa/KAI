"""Temporary governance update script for PH5B close / PH5C open."""
from pathlib import Path


def read(p: str) -> str:
    with open(p, "rb") as f:
        return f.read().decode("latin-1")


def write(p: str, t: str) -> None:
    with open(p, "w", encoding="utf-8") as f:
        f.write(t)


def update_decision_log() -> None:
    t = read("DECISION_LOG.md")
    append = (
        "\n"
        "### D-94 (2026-03-24): PH5B review accepted -- sprint closed\n"
        "- PH5B cluster analysis results accepted.\n"
        "- Root cause confirmed: EMPTY_MANUAL (19/19) -- source=Manual, content placeholder only.\n"
        "- No model failure; gap is at data-quality/ingestion layer.\n"
        "- Recommendation: FILTER_BEFORE_LLM is the right next intervention.\n"
        "- PH5B formally closed. TASKLIST PH5B-6 completed.\n"
        "\n"
        "### D-95 (2026-03-24): PH5C opened -- Stub Document Pre-Filter\n"
        "- Sprint defined: PH5C_FILTER_BEFORE_LLM_BASELINE\n"
        "- Objective: implement a pre-LLM stub/empty-content filter in the analysis pipeline.\n"
        "- Scope: detect and skip documents where content_len < threshold (proposed: 50 bytes).\n"
        "- Tag skipped documents as stub_document instead of sending to LLM.\n"
        "- Expected impact: reduce LLM-error-proxy rate from 27.5% toward 0% for stub cases.\n"
        "- Guardrail: threshold must not exclude valid short Manual docs -- validation required.\n"
        "- Guardrail: PH5D must not be opened before PH5C review is closed.\n"
        "- Contract: par85 added to docs/contracts.md.\n"
        "- baseline: 1615 passed, ruff clean, mypy 0 errors\n"
    )
    write("DECISION_LOG.md", t.rstrip() + "\n" + append)
    print("DECISION_LOG.md: D-94 + D-95 added")


def update_sprint_ledger() -> None:
    t = read("SPRINT_LEDGER.md")
    replacements = [
        (
            "phase_5_status: `active -- PH5B execution complete (D-93); results-review next`",
            "phase_5_status: `active -- PH5B closed (D-94); PH5C active (D-95)`",
        ),
        (
            "current_sprint: `PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS (active D-92, par84)`",
            "current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (active D-95, par85)`",
        ),
        (
            "next_required_step: `PH5B_RESULTS_REVIEW_AND_CLOSE`",
            "next_required_step: `PH5C_EXECUTION`",
        ),
        (
            "| PH5B | 2026-03-24 | **results-review** | Root cause: EMPTY_MANUAL 19/19 -- data quality gap, not model failure (D-93) |",
            (
                "| PH5B | 2026-03-24 | closed | Root cause: EMPTY_MANUAL 19/19 -- data quality gap, not model failure (D-94) |\n"
                "| PH5C | 2026-03-24 | **active** | Stub Document Pre-Filter -- skip LLM for empty Manual docs (D-95, par85) |"
            ),
        ),
    ]
    for old, new in replacements:
        t = t.replace(old, new)
    write("SPRINT_LEDGER.md", t)
    print("SPRINT_LEDGER.md updated")


def update_knowledge_base() -> None:
    t = read("KNOWLEDGE_BASE.md")
    replacements = [
        (
            "PH5B execution complete (D-93) | next_required_step: PH5B_RESULTS_REVIEW_AND_CLOSE",
            "PH5C active (D-95, par85) | next_required_step: PH5C_EXECUTION",
        ),
        (
            "current_sprint: `PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS (active D-92, \xa784)`",
            "current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (active D-95, par85)`",
        ),
        (
            "current_sprint: `PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS (active D-92, \xc2\xa784)`",
            "current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (active D-95, par85)`",
        ),
        (
            "next_required_step: `PH5B_RESULTS_REVIEW_AND_CLOSE`",
            "next_required_step: `PH5C_EXECUTION`",
        ),
        (
            "phase5_guardrail: PH5B active -- do not open PH5C before PH5B review closes",
            "phase5_guardrail: PH5C active -- do not open PH5D before PH5C review closes",
        ),
    ]
    for old, new in replacements:
        t = t.replace(old, new)

    # Replace PH5B section with PH5C section
    old_section = "## PH5B Execution Complete (D-93)"
    new_section = (
        "## PH5C Active (D-95, par85)\n\n"
        "- PH5A: LLM-error-proxy 27.5% (19/69 docs).\n"
        "- PH5B: root cause = EMPTY_MANUAL (19/19) -- source=Manual, content='Comments' (8 bytes).\n"
        "- PH5C objective: pre-LLM stub filter -- skip LLM for docs where content_len < 50 bytes.\n"
        "- Tag skipped docs as stub_document.\n"
        "- Expected: reduce proxy rate 27.5% -> ~0% for stub cases.\n"
        "- Script: scripts/ph5c_stub_filter_baseline.py"
    )
    if old_section in t:
        # Find the end of the old section (next ## heading)
        start = t.find(old_section)
        end_marker = "\n## "
        end = t.find(end_marker, start + len(old_section))
        if end == -1:
            end = len(t)
        old_block = t[start:end]
        t = t.replace(old_block, new_section + "\n")
    write("KNOWLEDGE_BASE.md", t)
    print("KNOWLEDGE_BASE.md updated")


def update_phase_plan() -> None:
    t = read("PHASE_PLAN.md")
    replacements = [
        (
            "next_required_step: `PH5B_RESULTS_REVIEW_AND_CLOSE`",
            "next_required_step: `PH5C_EXECUTION`",
        ),
    ]
    for old, new in replacements:
        t = t.replace(old, new)

    # Fix current_sprint regardless of encoding
    import re
    t = re.sub(
        r"current_sprint: `PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS \(active D-92, .{1,5}84\)`",
        "current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (active D-95, par85)`",
        t,
    )

    # Replace PH5B section header
    old_ph5b = "## PH5B Active (D-92,"
    new_ph5c = (
        "## PH5C Active (D-95, par85)\n\n"
        "- PH5A closed: reliability baseline (LLM-error-proxy 27.5%).\n"
        "- PH5B closed: root cause = EMPTY_MANUAL (19/19), data-quality gap (D-94).\n"
        "- PH5C focus: implement pre-LLM stub filter; tag empty docs as stub_document.\n"
        "- Script: scripts/ph5c_stub_filter_baseline.py\n"
        "- Threshold: content_len < 50 bytes (must validate against valid short docs)."
    )
    if old_ph5b in t:
        start = t.find(old_ph5b)
        end_marker = "\n## "
        end = t.find(end_marker, start + len(old_ph5b))
        if end == -1:
            end = len(t)
        t = t[:start] + new_ph5c + "\n" + t[end:]
    write("PHASE_PLAN.md", t)
    print("PHASE_PLAN.md updated")


def update_risk_register() -> None:
    t = read("RISK_REGISTER.md")
    t = t.replace(
        "next_required_step: `PH5B_RESULTS_REVIEW_AND_CLOSE`",
        "next_required_step: `PH5C_EXECUTION`",
    ).replace(
        "| R-PH5-002 | LLM error proxy 27.5% -- root cause unknown. | high | medium | PH5B active to classify root causes. | open (PH5B) |",
        (
            "| R-PH5-002 | LLM error proxy 27.5% -- root cause confirmed: EMPTY_MANUAL. | high | medium | PH5C active: stub filter to eliminate empty-doc proxy cases. | open (PH5C) |\n"
            "| R-PH5-003 | Stub threshold too broad may exclude valid short Manual docs. | medium | low | Validate threshold against all Manual docs before shipping. | open (PH5C) |\n"
            "| R-PH5-004 | PH5C scope drift into broader reliability refactoring. | low | low | Keep PH5C to stub-filter only; no keyword/scoring changes. | open (PH5C) |"
        ),
    )
    import re
    t = re.sub(
        r"current_sprint: `PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS \(active D-92, .{1,5}84\)`",
        "current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (active D-95, par85)`",
        t,
    )
    write("RISK_REGISTER.md", t)
    print("RISK_REGISTER.md updated")


def update_tasklist() -> None:
    t = read("TASKLIST.md")
    t = t.replace(
        "current_sprint: `PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS (execution complete)`",
        "current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (active D-95, par85)`",
    ).replace(
        "next_required_step: `PH5B_RESULTS_REVIEW_AND_CLOSE`",
        "next_required_step: `PH5C_EXECUTION`",
    ).replace(
        "- [ ] **Phase 5 / PH5B** (2026-03-24) -- Low Signal Cluster: root cause EMPTY_MANUAL 19/19, recommendation FILTER_BEFORE_LLM (pending close)",
        "- [x] **Phase 5 / PH5B** (2026-03-24) -- Low Signal Cluster: root cause EMPTY_MANUAL 19/19, recommendation FILTER_BEFORE_LLM (D-94)",
    )
    # Replace active tasks block
    old_block = (
        "## Active Tasks\n\n"
        "| Task | Status |\n"
        "|---|---|\n"
        "| PH5B-1 Cluster script `scripts/ph5b_cluster_analysis.py` | \u2705 |\n"
        "| PH5B-2 Cluster 19 proxy docs by content / source / topic | \u2705 |\n"
        "| PH5B-3 Root cause identified: EMPTY_MANUAL (19/19) | \u2705 |\n"
        "| PH5B-4 Artifact: `artifacts/ph5b/ph5b_cluster_analysis.json` | \u2705 |\n"
        "| PH5B-5 Artifact: `artifacts/ph5b/ph5b_operator_summary.md` | \u2705 |\n"
        "| PH5B-6 Governance docs + sprint close | \u2705 |"
    )
    new_block = (
        "## Active Tasks\n\n"
        "| Task | Status |\n"
        "|---|---|\n"
        "| PH5C-1 Script `scripts/ph5c_stub_filter_baseline.py` | \u2610 |\n"
        "| PH5C-2 Identify all stub docs (content_len < 50 bytes) | \u2610 |\n"
        "| PH5C-3 Validate threshold -- no valid short docs excluded | \u2610 |\n"
        "| PH5C-4 Compute projected proxy-rate reduction | \u2610 |\n"
        "| PH5C-5 Artifact: `artifacts/ph5c/ph5c_stub_filter_baseline.json` | \u2610 |\n"
        "| PH5C-6 Artifact: `artifacts/ph5c/ph5c_operator_summary.md` | \u2610 |\n"
        "| PH5C-7 Governance docs + sprint close | \u2610 |"
    )
    if old_block in t:
        t = t.replace(old_block, new_block)
    else:
        # Fallback: replace any active tasks section referencing PH5B tasks
        import re
        t = re.sub(
            r"## Active Tasks\n\n\| Task.*?\n(?:\|.*?\n)*",
            new_block + "\n",
            t,
            flags=re.DOTALL,
        )
    write("TASKLIST.md", t)
    print("TASKLIST.md updated")


def update_agents() -> None:
    t = read("AGENTS.md")
    import re
    t = re.sub(
        r"\| current_sprint \| `PH5[AB][^`]*` \|",
        "| current_sprint | `PH5C_FILTER_BEFORE_LLM_BASELINE (active D-95, par85)` |",
        t,
    ).replace(
        "| next_required_step | `PH5B definition` |",
        "| next_required_step | `PH5C_EXECUTION` |",
    ).replace(
        "| phase5_status | `active -- PH5A closed; PH5B definition next` |",
        "| phase5_status | `active -- PH5B closed (D-94); PH5C active (D-95)` |",
    ).replace(
        "| next_required_step | `PH5C_EXECUTION` |",
        "| next_required_step | `PH5C_EXECUTION` |",
    )
    write("AGENTS.md", t)
    print("AGENTS.md updated")


if __name__ == "__main__":
    update_decision_log()
    update_sprint_ledger()
    update_knowledge_base()
    update_phase_plan()
    update_risk_register()
    update_tasklist()
    update_agents()
    print("\nAll governance docs updated.")
