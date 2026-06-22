import { useEffect, useState } from "react";

// One shared "now" clock for all liveness indicators (NEO-P-204). Each <LiveDot>
// used to run its own setInterval(5s) + setState — on panel-dense pages that is
// ~17 independent timers all re-rendering. This collapses them into a SINGLE
// module-level interval with a subscriber set; the interval only runs while at
// least one component is subscribed and is torn down when the last unmounts.
// Granularity (5s) is identical to the old per-instance timer.

const TICK_MS = 5_000;

let current = Date.now();
const subscribers = new Set<(now: number) => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function ensureTimer(): void {
  if (timer != null) return;
  timer = setInterval(() => {
    current = Date.now();
    for (const cb of subscribers) cb(current);
  }, TICK_MS);
}

function maybeStopTimer(): void {
  if (subscribers.size === 0 && timer != null) {
    clearInterval(timer);
    timer = null;
  }
}

/** Current epoch-ms, updated every 5s from a single shared interval. */
export function useSharedNow(): number {
  const [now, setNow] = useState<number>(current);

  useEffect(() => {
    subscribers.add(setNow);
    ensureTimer();
    // Re-sync immediately: the shared clock may have advanced before this
    // component subscribed (e.g. mounted between ticks).
    setNow(current);
    return () => {
      subscribers.delete(setNow);
      maybeStopTimer();
    };
  }, []);

  return now;
}

// Test-only: deterministic control over the shared clock + teardown so unit
// tests do not leak the module-global interval/subscribers across cases.
export function __setSharedNowForTests(value: number): void {
  current = value;
  for (const cb of subscribers) cb(current);
}

export function __resetSharedNowForTests(): void {
  if (timer != null) {
    clearInterval(timer);
    timer = null;
  }
  subscribers.clear();
  current = Date.now();
}
