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
    expect(byKey.source.value).toBe("0/3 trusted");
    expect(byKey.source.tone).toBe("warn");
    expect(byKey.regime.tone).toBe("readonly");
    expect(byKey.signal.value).toBe("Lift n/a");
  });

  it("renders an unconfigured re-entry target as neutral (not an expired warning)", () => {
    const q = quality({
      reentry: {
        target_date: "2026-05-16",
        today: "2026-06-22",
        status: "no_active_target",
        days_delta: -37,
        warning: "Kein aktives Re-Entry-Target gesetzt — Konfiguration ausstehend.",
        target_source: "default_historical",
      },
    });
    const byKey = Object.fromEntries(deriveTruthChips(q, regime, gate()).map((c) => [c.key, c]));
    expect(byKey.reentry.value).toBe("nicht gesetzt");
    expect(byKey.reentry.tone).toBe("muted");
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

  it("returns UNDERPERFORMING (critical) when lift inverse — worse than merely unproven", () => {
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
    expect(r.tone).toBe("critical");
    expect(r.detail).toContain("invers");
  });

  it("returns UNPROVEN (warn, not critical) when a tier bucket is empty", () => {
    const r = resolvePriorityVerdict(
      gate({
        heartbeat_status: "active",
        priority_quality: { current_quality_verdict: "priority_unproven" },
      }),
    );
    expect(r.verdict).toBe("UNPROVEN");
    expect(r.tone).toBe("warn");
    expect(r.detail).toContain("nicht invers");
  });

  it("returns INSUFFICIENT (warn) when no lift is measurable", () => {
    const r = resolvePriorityVerdict(
      gate({
        heartbeat_status: "active",
        priority_quality: { current_quality_verdict: "insufficient_data" },
      }),
    );
    expect(r.verdict).toBe("INSUFFICIENT");
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

describe("entryModeChip (S6, #157 Scope-Luecke + D-233-Modi)", () => {
  it("zeigt disabled mit Alias-Routen als kontrollierten Info-Zustand", () => {
    const q = quality({
      runtime: {
        entry_mode: "disabled",
        entry_mode_label: "disabled — Kill-Switch",
        autonomous_loop_open: false,
        open_routes: [
          { route: "premium_paper", alias_used: "premium_three_arm_ack" },
          { route: "real_analysis_paper", alias_used: "real_analysis_three_arm_ack" },
        ],
        contradictions: [],
      },
    });
    const chip = deriveTruthChips(q, regime, gate()).find((c) => c.key === "entry-mode")!;
    expect(chip.value).toBe("disabled · 2 Route(n) via Ack");
    expect(chip.tone).toBe("info");
    expect(chip.hint).toContain("D-233");
  });

  it("zeigt disabled ohne offene Routen als warn (alles zu)", () => {
    const q = quality({
      runtime: {
        entry_mode: "disabled",
        entry_mode_label: "disabled — Kill-Switch",
        autonomous_loop_open: false,
        open_routes: [],
        contradictions: [],
      },
    });
    const chip = deriveTruthChips(q, regime, gate()).find((c) => c.key === "entry-mode")!;
    expect(chip.value).toBe("disabled · alles zu");
    expect(chip.tone).toBe("warn");
  });

  it("zeigt die neuen D-233-Modi als info mit Label", () => {
    const q = quality({
      runtime: {
        entry_mode: "paper_learning",
        entry_mode_label: "paper-learning (Premium + Real-Analysis)",
        autonomous_loop_open: false,
        open_routes: [
          { route: "premium_paper", alias_used: null },
          { route: "real_analysis_paper", alias_used: null },
        ],
        contradictions: [],
      },
    });
    const chip = deriveTruthChips(q, regime, gate()).find((c) => c.key === "entry-mode")!;
    expect(chip.value).toBe("paper-learning (Premium + Real-Analysis)");
    expect(chip.tone).toBe("info");
    expect(chip.hint).toContain("Loop zu");
  });

  it("zeigt Kontradiktionen als critical", () => {
    const q = quality({
      runtime: {
        entry_mode: "paper_premium_limited",
        entry_mode_label: "paper (nur Premium-Route)",
        open_routes: [],
        contradictions: ["fastlane_enabled_in_limited_paper_mode"],
      },
    });
    const chip = deriveTruthChips(q, regime, gate()).find((c) => c.key === "entry-mode")!;
    expect(chip.tone).toBe("critical");
    expect(chip.value).toContain("KONTRADIKTION");
  });

  it("degradiert ehrlich ohne Runtime-Block", () => {
    const chip = deriveTruthChips(quality(), regime, gate()).find((c) => c.key === "entry-mode")!;
    expect(chip.value).toBe("unbekannt");
    expect(chip.tone).toBe("muted");
  });
});

describe("shadowAttributionChip (S6 Canary-Attribution)", () => {
  it("warnt, wenn alle 24h-Kandidaten Proben sind (Vorfallklasse 2026-06-03)", () => {
    const q = quality({ shadow_attribution: { real_candidates_24h: 0, probe_candidates_24h: 42 } });
    const chip = deriveTruthChips(q, regime, gate()).find((c) => c.key === "shadow-attribution")!;
    expect(chip.value).toBe("0 real · 42 probe");
    expect(chip.tone).toBe("warn");
  });

  it("zeigt real-vs-probe als info, sobald echte Kandidaten da sind", () => {
    const q = quality({ shadow_attribution: { real_candidates_24h: 7, probe_candidates_24h: 393 } });
    const chip = deriveTruthChips(q, regime, gate()).find((c) => c.key === "shadow-attribution")!;
    expect(chip.value).toBe("7 real · 393 probe");
    expect(chip.tone).toBe("info");
  });
});

describe("sourceReliabilityChip (Frühphasen-Evidenz, kein Integritätsbruch)", () => {
  it("zeigt 0 trusted quantifiziert als warn statt critical", () => {
    const chip = deriveTruthChips(quality(), regime, gate()).find((c) => c.key === "source")!;
    expect(chip.value).toBe("0/3 trusted");
    expect(chip.tone).toBe("warn");
    expect(chip.hint).toContain("fail-closed");
    expect(chip.hint).not.toContain("institutionell");
  });
});

describe("signalQualityChip (Tier-Lift-konsistent statt Low-P-Sackgasse)", () => {
  it("zeigt positiven Tier-Lift als ok, unabhängig von der Low-P-Baseline", () => {
    const q = quality({ priority_tier_lift_pct: 4.2, low_priority_hit_rate_pct: null });
    const chip = deriveTruthChips(q, regime, gate()).find((c) => c.key === "signal")!;
    expect(chip.value).toBe("Lift +4.2pp");
    expect(chip.tone).toBe("ok");
    expect(chip.hint).toContain("by-design nicht messbar");
  });

  it("zeigt nicht-positiven Tier-Lift als warn", () => {
    const q = quality({ priority_tier_lift_pct: -1.5 });
    const chip = deriveTruthChips(q, regime, gate()).find((c) => c.key === "signal")!;
    expect(chip.value).toBe("Lift -1.5pp");
    expect(chip.tone).toBe("warn");
  });

  it("zeigt fehlenden Lift als 'Lift n/a', nicht als Low-P-Gate", () => {
    const q = quality({ priority_tier_lift_pct: null });
    const chip = deriveTruthChips(q, regime, gate()).find((c) => c.key === "signal")!;
    expect(chip.value).toBe("Lift n/a");
    expect(chip.tone).toBe("warn");
  });
});
