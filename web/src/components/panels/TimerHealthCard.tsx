import { ShieldAlert, ShieldCheck, Clock, Activity } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { fetchTimerHealth, type TimerHealthResponse } from "@/lib/api";
import { formatRelative, formatAbsolute } from "@/lib/time";
import { usePolling } from "@/lib/usePolling";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;

function getTimerHealthTone(state: TimerHealthResponse["state"]): "pos" | "warn" | "neg" | "muted" {
  if (state === "ok") return "pos";
  if (state === "critical") return "neg";
  if (state === "has_inactive") return "warn";
  if (state === "stale" || state === "corrupt") return "warn";
  return "muted";
}

function getTimerHealthLabel(state: TimerHealthResponse["state"]): string {
  if (state === "ok") return "Aktiv";
  if (state === "critical") return "Kritischer Timer-Fehler";
  if (state === "has_inactive") return "Inaktive Timer";
  if (state === "stale") return "Verzögert (>2h)";
  if (state === "corrupt") return "Log-Fehler";
  return "Keine Daten";
}

export function TimerHealthCard() {
  const state = usePolling<TimerHealthResponse>(fetchTimerHealth, {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });

  const health = state.state === "ready" ? state.data : null;
  const tone = health ? getTimerHealthTone(health.state) : "muted";
  // FS-2 (#198): separate genuinely-failed timers from expected-inactive one-shots.
  const inactive = health?.inactive ?? [];
  const criticalEntries = inactive.filter((i) => i.severity === "critical");
  const expectedEntries = inactive.filter((i) => i.severity === "expected_inactive");
  const criticalCount = health?.critical_count ?? criticalEntries.length;

  return (
    <Card padded>
      <CardHeader
        title="Timer-Gesundheit"
        subtitle="Überwachung der systemd-Timer und Hintergrund-Cronjobs auf dem Pi"
        right={
          health ? (
            <Badge tone={tone} dot>
              <Activity size={10} />
              {getTimerHealthLabel(health.state)}
            </Badge>
          ) : undefined
        }
      />

      {state.state === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">
          Lade Timer-Gesundheitsstatus …
        </div>
      )}

      {state.state === "error" && (
        <div className="py-3 text-xs text-neg break-words">
          Verbindung zum Timer-Healthcheck fehlgeschlagen: {state.error.message}
        </div>
      )}

      {state.state === "ready" && health && (
        <div className="space-y-4 font-mono">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div className="p-2.5 bg-bg-2 rounded-sm border border-line-subtle">
              <div className="text-2xs text-fg-subtle uppercase tracking-wide">Status</div>
              <div
                className={cn(
                  "text-xs font-semibold mt-1 uppercase",
                  tone === "pos" ? "text-pos" : tone === "warn" ? "text-warn" : tone === "neg" ? "text-neg" : "text-fg-subtle",
                )}
              >
                {getTimerHealthLabel(health.state)}
              </div>
            </div>
            <div className="p-2.5 bg-bg-2 rounded-sm border border-line-subtle">
              <div className="text-2xs text-fg-subtle uppercase tracking-wide">Überwacht</div>
              <div className="text-xs font-semibold text-fg mt-1">{health.total} Timer</div>
            </div>
            <div className="p-2.5 bg-bg-2 rounded-sm border border-line-subtle">
              <div className="text-2xs text-fg-subtle uppercase tracking-wide">Aktiv</div>
              <div className="text-xs font-semibold text-pos mt-1">{health.active} OK</div>
            </div>
            <div className="p-2.5 bg-bg-2 rounded-sm border border-line-subtle">
              <div className="text-2xs text-fg-subtle uppercase tracking-wide">Ausgefallen</div>
              <div
                className={cn(
                  "text-xs font-semibold mt-1",
                  criticalCount > 0 ? "text-neg animate-pulse font-bold" : "text-fg-muted",
                )}
              >
                {criticalCount} Fehler
              </div>
            </div>
          </div>

          {criticalEntries.length > 0 && (
            <div className="rounded-sm border border-neg/30 bg-neg/5 p-3 space-y-2">
              <div className="text-2xs font-semibold text-neg uppercase tracking-wider flex items-center gap-1.5">
                <ShieldAlert size={12} className="animate-bounce shrink-0" />
                Kritisch: Recurring/failed Timer inaktiv!
              </div>
              <div className="space-y-1.5">
                {criticalEntries.map((item, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between p-2 rounded-sm bg-bg-1 border border-line-subtle text-2xs gap-2"
                  >
                    <span className="font-semibold text-fg truncate flex-1" title={item.unit}>
                      {item.unit}
                    </span>
                    <Badge tone="neg" dot className="uppercase font-semibold shrink-0">
                      {item.state}
                    </Badge>
                  </div>
                ))}
              </div>
            </div>
          )}

          {expectedEntries.length > 0 && (
            <div className="rounded-sm border border-line-subtle bg-bg-2 p-3 space-y-2">
              <div className="text-2xs font-semibold text-fg-subtle uppercase tracking-wider flex items-center gap-1.5">
                <Clock size={12} className="shrink-0" />
                Erwartbar inaktiv (One-Shot nach Lauf) — kein Fehler
              </div>
              <div className="space-y-1.5">
                {expectedEntries.map((item, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between p-2 rounded-sm bg-bg-1 border border-line-subtle text-2xs gap-2"
                  >
                    <span className="text-fg-muted truncate flex-1" title={item.unit}>
                      {item.unit}
                    </span>
                    <Badge tone="muted" className="uppercase shrink-0">
                      one-shot
                    </Badge>
                  </div>
                ))}
              </div>
            </div>
          )}

          {health.state === "ok" && (
            <div className="rounded-sm border border-pos/20 bg-pos/5 p-2.5 flex items-center gap-2 text-2xs text-pos">
              <ShieldCheck size={14} className="shrink-0" />
              <span>Alle Hintergrund-Timer laufen ordnungsgemäß auf dem Pi 5.</span>
            </div>
          )}

          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between text-2xs text-fg-subtle border-t border-line-subtle pt-2 gap-2">
            <span>
              Letzte Messung: {health.checked_at ? formatAbsolute(health.checked_at) : "Keine"}
              {health.checked_at && ` (${formatRelative(health.checked_at)})`}
            </span>
            {health.stale_minutes !== null && health.stale_minutes > 120 && (
              <span className="text-warn font-semibold flex items-center gap-1 shrink-0">
                <Clock size={10} className="animate-pulse" />
                Protokoll veraltet ({health.stale_minutes} Min. alt)
              </span>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
