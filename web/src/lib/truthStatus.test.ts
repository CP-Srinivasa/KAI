import { describe, it, expect } from "vitest";
import {
  deriveTruthChips,
  resolvePriorityVerdict,
  highestTruthTone,
} from "./truthStatus";
import type {
  DashboardQuality,
  DashboardRegime,
  PriorityGateSummary,
} from "@/lib/api";

function quality(over: Partial<DashboardQuality> = {}): DashboardQuality {
  return {
    reentry: { target_date: "2026-05-16", today: "2026-06-04", status: "expired", days_delta: -19, warning: null },
    paper_fills_with_pnl: 144,
    paper_evidence: {
      scope: "lifetime",
      since: null,
      until: "2026-06-04T00:00:00Z",
      window_hours: 24,
      fills_total: 144,
      fills_recent_24h: 0,
      closed_total: 144,
      closed_recent_24h: 0,
      realized_pnl_total_usd: 12,
      realized_pnl_recent_24h_usd: 0,
      expectancy_usd: null,
      win_rate_pct: null,
      avg_win_usd: null,
      avg_loss_usd: null,
      fees_slippage_included: "unknown",
      source_artifact: "x",
      source_artifact_updated_at: null,
      stale_status: "fresh",
      quality_status: "ok",
      warning: null,
    },
    low_priority_hit_rate_pct: null,
    priority_tier_lift_pct: null,
    source_reliability: {
      status: "ok",
      generated_at: null,
      window_days: 14,
      quality_status: "warning",
      health_warning: "no trusted sources",
      trusted_count: 0,
      source_count: 3,
      tier_counts: { trusted: 0, watch: 1, low: 1, insufficient: 1 },
      top_sources: [],
      unknown_bucket: null,
    },
    ...over,
  } as DashboardQuality;
}

const regime = {
  generated_at: "2026-06-04T10:00:00Z",
  is_read_only: true,
  by_asset: { BTC: { asset: "BTC", timestamp: "x", regime: "chop_quiet", vol_class: "vol_low", confidence: 1 } },
} as unknown as DashboardRegime;

function gate(over: Partial<PriorityGateSummary> = {}): PriorityGateSummary {
  return {
    report_type: "priority_gate",
    threshold: 1,
    gate_active: true,
    window_hours: 24,
    total_cycles: 0,
    priority_rejected: 0,
    other_rejected: 0,
    completed: 0,
    heartbeat_status: "unknown",
    window_start_utc: "x",
    audit_path: "x",
    ...over,
  } as PriorityGateSummary;
}

describe("deriveTruthChips", () => {
  it("marks expired re-entry, historical paper, 0 trusted, read-only regime", () => {
    const chips = deriveTruthChips(quality(), regime, gate());
    const byKey = Object.fromEntries(chips.map((c) => [c.key, c]));
    expect(byKey.reentry.value).toBe("abgelaufen");
    expect(byKey.reentry.tone).toBe("warn");
    expect(byKey.paper.value).toContain("144 hist");
    expect(byKey.paper.value).toContain("0 / 24h");
    expect(byKey.source.value).toBe("0 trusted");
    expect(byKey.source.tone).toBe("critical");
    expect(byKey.regime.tone).toBe("readonly");
    expect(byKey.signal.value).toBe("Low-P insufficient");
  });

  it("sorts critical tones before healthy/read-only", () => {
    const chips = deriveTruthChips(quality(), regime, gate());
    const firstCritical = chips.findIndex((c) => c.tone === "critical");
    const firstReadonly = chips.findIndex((c) => c.tone === "readonly");
    expect(firstCritical).toBeGreaterThanOrEqual(0);
    expect(firstCritical).toBeLessThan(firstReadonly);
  });

  it("shows 24h-first paper label when recent fills exist", () => {
    const q = quality({
      paper_evidence: {
        ...quality().paper_evidence!,
        fills_recent_24h: 3,
      },
    });
    const chips = deriveTruthChips(q, regime, gate({ heartbeat_status: "active" }));
    const paper = chips.find((c) => c.key === "paper")!;
    expect(paper.value).toContain("3 / 24h");
    expect(paper.tone).toBe("info");
  });
});

describe("resolvePriorityVerdict", () => {
  it("returns HEARTBEAT UNKNOWN (critical) when loop unverified", () => {
    const r = resolvePriorityVerdict(gate({ heartbeat_status: "unknown" }));
    expect(r.verdict).toBe("HEARTBEAT UNKNOWN");
    expect(r.tone).toBe("critical");
  });

  it("returns STALE when audit stale", () => {
    const r = resolvePriorityVerdict(gate({ heartbeat_status: "stale" }));
    expect(r.verdict).toBe("STALE");
    expect(r.tone).toBe("warn");
  });

  it("returns ACTIVE BLOCKING when validated + blocking", () => {
    const r = resolvePriorityVerdict(
      gate({
        heartbeat_status: "active_blocking",
        priority_quality: { current_quality_verdict: "priority_validated" },
      }),
    );
    expect(r.verdict).toBe("ACTIVE BLOCKING");
    expect(r.tone).toBe("info");
  });

  it("returns UNDERPERFORMING (warn) when lift negative", () => {
    const r = resolvePriorityVerdict(
      gate({
        heartbeat_status: "active",
        priority_quality: {
          current_quality_verdict: "priority_underperforming",
          high_priority_lift_pct: -3,
        },
      }),
    );
    expect(r.verdict).toBe("UNDERPERFORMING");
    expect(r.tone).toBe("warn");
  });

  it("returns UNKNOWN (muted) when summary missing", () => {
    const r = resolvePriorityVerdict(null);
    expect(r.verdict).toBe("UNKNOWN");
    expect(r.tone).toBe("muted");
  });
});

describe("highestTruthTone", () => {
  it("picks the most urgent tone", () => {
    expect(highestTruthTone(deriveTruthChips(quality(), regime, gate()))).toBe("critical");
  });
});
