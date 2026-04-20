import { Bell } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useDashboardQuality } from "@/lib/useDashboardQuality";
import { cn } from "@/lib/utils";

const LAST_SEEN_KEY = "kai-bell-lastseen";

function readLastSeen(): string {
  try {
    return localStorage.getItem(LAST_SEEN_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeLastSeen(value: string) {
  try {
    localStorage.setItem(LAST_SEEN_KEY, value);
  } catch {}
}

export function NotificationsBell() {
  const q = useDashboardQuality(60_000);
  const [open, setOpen] = useState(false);
  const [lastSeen, setLastSeen] = useState<string>(() => readLastSeen());
  const panelRef = useRef<HTMLDivElement | null>(null);

  const alerts = q.state === "ready" ? q.data.recent_alerts : [];

  // Newest first — backend already reverses but we re-sort defensively.
  const sorted = useMemo(
    () =>
      [...alerts].sort((a, b) => (b.dispatched_at || "").localeCompare(a.dispatched_at || "")),
    [alerts],
  );

  const newest = sorted[0]?.dispatched_at ?? "";
  const unseenCount = useMemo(
    () => sorted.filter((a) => (a.dispatched_at || "") > lastSeen).length,
    [sorted, lastSeen],
  );

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!panelRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && newest) {
      writeLastSeen(newest);
      setLastSeen(newest);
    }
  };

  const showDot = unseenCount > 0;

  return (
    <div className="relative" ref={panelRef}>
      <button
        onClick={toggle}
        className="relative h-8 w-8 grid place-items-center rounded-sm border border-line-subtle bg-bg-2 text-fg-muted hover:text-fg hover:bg-bg-3 transition-colors"
        aria-label={`Notifications${unseenCount > 0 ? ` (${unseenCount} neu)` : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <Bell size={14} />
        {showDot && (
          <span className="absolute top-1.5 right-1.5 h-1.5 w-1.5 rounded-full bg-neg ring-2 ring-bg-2" />
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Notifications"
          className="absolute right-0 top-full mt-1.5 z-40 w-[360px] max-w-[90vw] rounded-md border border-line bg-bg-1 shadow-raised overflow-hidden"
        >
          <div className="flex items-center justify-between px-3 py-2 border-b border-line-subtle">
            <span className="text-xs font-semibold tracking-tight text-fg">
              Benachrichtigungen
            </span>
            <span className="text-2xs text-fg-subtle font-mono">
              {q.state === "ready" ? `${sorted.length} aktuell` : "—"}
            </span>
          </div>

          <div className="max-h-[420px] overflow-y-auto">
            {q.state === "loading" && (
              <div className="px-3 py-6 text-center text-xs text-fg-subtle">Lade…</div>
            )}
            {q.state === "error" && (
              <div className="px-3 py-6 text-center text-xs text-neg">
                {q.error.kind} · {q.error.message}
              </div>
            )}
            {q.state === "ready" && sorted.length === 0 && (
              <div className="px-3 py-6 text-center text-xs text-fg-subtle">
                Keine aktuellen Alerts.
              </div>
            )}
            {q.state === "ready" &&
              sorted.map((a) => {
                const tone =
                  a.outcome === "hit"
                    ? "text-pos"
                    : a.outcome === "miss"
                      ? "text-neg"
                      : "text-fg-muted";
                return (
                  <div
                    key={a.doc_id + a.dispatched_at}
                    className="px-3 py-2 border-b border-line-subtle/60 hover:bg-bg-2 text-xs"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono font-semibold truncate">
                        {a.assets?.length ? a.assets.join(", ") : a.doc_id}
                      </span>
                      <span className="text-2xs text-fg-subtle font-mono shrink-0">
                        {a.dispatched_at}
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 text-2xs font-mono">
                      <span className="text-fg-muted">
                        {a.sentiment || "—"} · prio {a.priority ?? "—"}
                      </span>
                      <span className={cn("ml-auto uppercase", tone)}>
                        {a.outcome || "open"}
                      </span>
                    </div>
                  </div>
                );
              })}
          </div>

          <div className="px-3 py-2 border-t border-line-subtle text-2xs text-fg-subtle">
            Quelle: /dashboard/api/quality · Stand: {q.state === "ready" ? q.data.generated_at : "—"}
          </div>
        </div>
      )}
    </div>
  );
}
