import { fetchDashboardRegime, type DashboardRegime } from "./api";
import { usePolling, type PollingState } from "./usePolling";

export type RegimeState = PollingState<DashboardRegime>;

export function useDashboardRegime(refreshMs: number = 60_000): RegimeState {
  return usePolling<DashboardRegime>(fetchDashboardRegime, {
    intervalMs: refreshMs,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
}
