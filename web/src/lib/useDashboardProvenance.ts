import { fetchDashboardProvenance, type DashboardProvenance } from "./api";
import { usePolling, type PollingState } from "./usePolling";

export type ProvenanceState = PollingState<DashboardProvenance>;

export function useDashboardProvenance(refreshMs: number = 60_000): ProvenanceState {
  return usePolling<DashboardProvenance>(fetchDashboardProvenance, {
    intervalMs: refreshMs,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
}
