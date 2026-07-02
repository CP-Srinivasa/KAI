"""Generate PH5 hold metrics report from local artifacts."""

from __future__ import annotations

from pathlib import Path

from app.alerts.hold_metrics import build_hold_metrics_report, write_hold_metrics_report

ARTIFACTS = Path("artifacts")
OUT_DIR = ARTIFACTS / "ph5_hold"


def main() -> None:
    report = build_hold_metrics_report(
        alert_audit_path=ARTIFACTS / "alert_audit.jsonl",
        alert_outcomes_path=ARTIFACTS / "alert_outcomes.jsonl",
        trading_loop_audit_path=ARTIFACTS / "trading_loop_audit.jsonl",
        paper_execution_audit_path=ARTIFACTS / "paper_execution_audit.jsonl",
    )
    json_out, md_out = write_hold_metrics_report(report, output_dir=OUT_DIR)

    gate = report["hold_gate_evaluation"]
    hit = report["alert_hit_rate_evidence"]
    print("PH5 hold metrics report written:")
    print(f"  {json_out}")
    print(f"  {md_out}")
    print(
        "Gate status: "
        f"{gate['overall_status']} "
        f"(resolved_directional={hit['resolved_directional_documents']}/"
        f"{hit['minimum_resolved_directional_alerts_for_gate']})"
    )


if __name__ == "__main__":
    main()
