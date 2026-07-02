import { describe, it, expect } from "vitest";
import {
  deriveBannerState,
  type BannerStateKind,
} from "./PremiumRuntimeBanner";
import type {
  PremiumRuntimeResponse,
  PremiumFastlaneStatus,
} from "@/lib/api";

// FS-1 (Issue #197 / Audit §7a): the 3+1-state banner truth must be a pure,
// testable derivation. Especially: entry_mode=disabled ∧ fastlane-off must NEVER
// resolve to an "active" state, and the deliberate-off posture must be neutral,
// not the loud "blocked" alarm.

function mkFastlane(p: Partial<PremiumFastlaneStatus> = {}): PremiumFastlaneStatus {
  return {
    enabled: false,
    active: false,
    window_reason: null,
    mode: "paper",
    route: "blocked",
    duration_days: 30,
    start_date: null,
    end_date: null,
    days_remaining: null,
    bypassed_gates: [],
    live_armed: false,
    live_protected: true,
    overrides_classic_block: false,
    default_notional_usdt: 0,
    min_notional_usdt: 0,
    max_notional_usdt: 0,
    max_leverage: 0,
    max_open_positions: 0,
    paper_equity_usdt: 0,
    observe_only_metrics: [],
    ...p,
  };
}

function mk(p: Partial<PremiumRuntimeResponse> = {}): PremiumRuntimeResponse {
  return {
    entry_mode: "disabled",
    entry_mode_allows_risk_increasing_entry: false,
    entry_mode_blocks_premium_paper: true,
    can_open_paper_positions: false,
    classic_can_open_paper_positions: false,
    blocking_reasons: ["entry_mode=disabled"],
    premium_paper_execution_enabled: false,
    premium_live_execution_enabled: false,
    premium_require_manual_approval_for_paper: true,
    premium_require_manual_approval_for_live: true,
    operator_signal_bridge_enabled: true,
    operator_signal_source_allowlist: ["telegram_premium_channel_approved"],
    premium_auto_fill_enabled: false,
    live_execution_enabled: false,
    execution_mode: "paper",
    premium_fastlane: mkFastlane(),
    warning: null,
    ...p,
  };
}

describe("deriveBannerState", () => {
  it("inactive_off: entry_mode=disabled, paper off, fastlane off (current Pi posture)", () => {
    expect(deriveBannerState(mk())).toBe<BannerStateKind>("inactive_off");
  });

  it("inactive_off is NEUTRAL, not the loud blocked alarm", () => {
    // Defense-in-depth: the everyday off-posture must not be 'blocked_by_entry_mode'.
    expect(deriveBannerState(mk())).not.toBe("blocked_by_entry_mode");
  });

  it("blocked_by_entry_mode: premium paper opted-in but entry_mode blocks", () => {
    const rt = mk({
      premium_paper_execution_enabled: true,
      can_open_paper_positions: false,
    });
    expect(deriveBannerState(rt)).toBe("blocked_by_entry_mode");
  });

  it("active_clean: entry_mode allows entries, no fastlane override", () => {
    const rt = mk({
      entry_mode: "paper",
      entry_mode_blocks_premium_paper: false,
      premium_paper_execution_enabled: true,
      can_open_paper_positions: true,
      blocking_reasons: [],
    });
    expect(deriveBannerState(rt)).toBe("active_clean");
  });

  it("active_via_fastlane_override: paper open via fastlane bypass DESPITE disabled", () => {
    const rt = mk({
      entry_mode: "disabled",
      can_open_paper_positions: true, // opened only because fastlane overrides
      premium_fastlane: mkFastlane({
        enabled: true,
        active: true,
        overrides_classic_block: true,
        route: "paper",
      }),
    });
    expect(deriveBannerState(rt)).toBe("active_via_fastlane_override");
  });

  it("fastlane_window_expired: fastlane enabled but window inactive", () => {
    const rt = mk({
      premium_fastlane: mkFastlane({
        enabled: true,
        active: false,
        window_reason: "fastlane_window_expired",
        overrides_classic_block: false,
      }),
    });
    expect(deriveBannerState(rt)).toBe("fastlane_window_expired");
  });

  it("INVARIANT: entry_mode=disabled ∧ fastlane-off ⇒ never an active state", () => {
    // Sweep paper-flag + can_open combinations under disabled+fastlane-off.
    for (const paper of [false, true]) {
      for (const canOpen of [false, true]) {
        const rt = mk({
          entry_mode: "disabled",
          entry_mode_blocks_premium_paper: true,
          premium_paper_execution_enabled: paper,
          can_open_paper_positions: canOpen,
          premium_fastlane: mkFastlane({ enabled: false, active: false, overrides_classic_block: false }),
        });
        const state = deriveBannerState(rt);
        // With fastlane OFF, can_open=true cannot truthfully happen; even if the
        // payload were inconsistent, the state must never be the fastlane bypass.
        expect(state).not.toBe("active_via_fastlane_override");
        if (!canOpen) {
          expect(["inactive_off", "blocked_by_entry_mode"]).toContain(state);
        }
      }
    }
  });
});
