import { fetchDashboardQuality, type DashboardQuality } from "./api";
import { usePolling, type PollingState } from "./usePolling";

export type QualityState = PollingState<DashboardQuality>;

export function useDashboardQuality(refreshMs: number = 30_000): QualityState {
  return usePolling<DashboardQuality>(fetchDashboardQuality, {
    intervalMs: refreshMs,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
}
