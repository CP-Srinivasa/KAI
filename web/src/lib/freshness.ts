// Single source for driving <LiveDot> from a useApi/usePolling state (WP UI-Step2
// G4.1). The dashboard had many real-but-static-looking panels: they polled every
// 30–60s but showed no freshness, so the operator could not tell live data from a
// frozen view. This maps a hook state -> LiveDot props uniformly:
//   - non-ready states pass through (loading/error -> LiveDot renders lädt/offline)
//   - ready: prefer the server-side generated_at (true data freshness); fall back
//     to the client fetchedAt (last successful poll) so there is never a silent
//     stale-freeze.
// fetchedAt is epoch-ms (from useApi/usePolling). new Date(ms) is deterministic.

export type FreshnessSource = {
  state: "loading" | "ready" | "error";
  fetchedAt?: number;
};

export type LiveDotInput = {
  state: "loading" | "ready" | "error";
  generatedAt: string | null;
};

export function liveDotProps(
  s: FreshnessSource,
  serverGeneratedAt?: string | null,
): LiveDotInput {
  if (s.state !== "ready") {
    return { state: s.state, generatedAt: null };
  }
  const generatedAt =
    (serverGeneratedAt && serverGeneratedAt.trim() ? serverGeneratedAt : null) ??
    (typeof s.fetchedAt === "number" ? new Date(s.fetchedAt).toISOString() : null);
  return { state: "ready", generatedAt };
}
