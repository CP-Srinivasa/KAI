import { fetchPriorityGate, type PriorityGateSummary } from "./api";
import { usePolling, type PollingState } from "./usePolling";

export type PriorityGateState = PollingState<PriorityGateSummary>;

export function usePriorityGate(refreshMs: number = 60_000): PriorityGateState {
  return usePolling<PriorityGateSummary>(fetchPriorityGate, {
    intervalMs: refreshMs,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
}
