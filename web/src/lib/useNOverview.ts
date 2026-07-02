import { fetchNOverview, type NOverview } from "./api";
import { usePolling, type PollingState } from "./usePolling";

export type NOverviewState = PollingState<NOverview>;

// Die fünf n ändern sich langsam (Ledger wächst über Stunden) — 60 s reicht.
export function useNOverview(refreshMs: number = 60_000): NOverviewState {
  return usePolling<NOverview>(fetchNOverview, {
    intervalMs: refreshMs,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
}
