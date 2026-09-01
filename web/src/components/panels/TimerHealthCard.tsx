// @data-source: /health/timers
import { ShieldAlert, ShieldCheck, Clock, Activity, HelpCircle } from "lucide-react";
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

/**
 * STAB-2026-09-01 §12: the label no longer hard-codes ">2h". The budget comes
 * from the backend, which derives it from the producer unit's own cadence
 * (`OnCalendar=*-*-* 04:30:00 UTC` => 24 h, not 2 h).
 */
function formatBudget(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "unbekanntes Budget";
  if (seconds >= 86_400) {
    const days = seconds / 86_400;
    return `>${days % 1 === 0 ? days : days.toFixed(1)} d`;
  }
  if (seconds >= 3_600) {
    const hours = seconds / 3_600;
    return `>${hours % 1 === 0 ? hours : hours.toFixed(1)} h`;
  }
  return `>${Math.round(seconds / 60)} min`;
}

function getTimerHealthLabel(health: TimerHealthResponse): string {
  const { state } = health;
  if (state === "ok") return "Aktiv";
  if (state === "critical") return "Kritischer Timer-Fehler";
  if (state === "has_inactive") return "Inaktive Timer";
  if (state === "stale") return `Veraltet (${formatBudget(health.freshness?.stale_after_seconds)})`;
  if (state === "corrupt") return "Log-Fehler";
  return "Keine Daten";
}

/**
 * A metric that may be UNKNOWN. The whole point of §11: when the snapshot no
 * longer describes the present, the card must say so instead of re-printing the
 * last good number in green as though it were a live status.
 */
function CountTile({
  label,
  value,
  lastKnown,
  isCurrent,
  tone = "fg",
}: {
  label: string;
  value: number | null | undefined;
  lastKnown?: number | null;
  isCurrent: boolean;
  tone?: "fg" | "pos" | "neg";
}) {
  const showUnknown = !isCurrent || value === null || value === undefined;
  return (
    <div className="p-2.5 bg-bg-2 rounded-sm border border-line-subtle">
      <div className="text-2xs text-fg-subtle uppercase tracking-wide">{label}</div>
      {showUnknown ? (
        <>
          <div className="text-xs font-semibold text-fg-subtle mt-1 uppercase flex items-center gap-1">
            <HelpCircle size={11} className="shrink-0" />
            Unbekannt
          </div>
          {lastKnown !== null && lastKnown !== undefined && (
            <div className="text-2xs text-fg-subtle mt-0.5">zuletzt bekannt: {lastKnown}</div>
          )}
        </>
      ) : (
        <div
          className={cn(
            "text-xs font-semibold mt-1",
            tone === "pos" ? "text-pos" : tone === "neg" ? "text-neg" : "text-fg",
          )}
        >
          {value}
        </div>
      )}
    </div>
  );
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

  // STAB-2026-09-01 §11: the single decision that governs this whole card. A
  // stale / corrupt / absent snapshot yields counts_are_current=false, and every
  // count below then renders UNKNOWN rather than the last green number.
  const countsAreCurrent = health?.counts_are_current !== false;
  const monitored = health?.monitored_timer_count ?? null;
  const installed = health?.installed_timer_count ?? null;

  return (
    <Card padded>
      <CardHeader
        title="Kritische Timer"
        subtitle={
          // §13: the monitored set is a SUBSET of the installed fleet. Naming both
          // "Timer" implied fleet-wide coverage the probe never had.
          installed !== null && monitored !== null
            ? `${monitored} überwachte von ${installed} installierten kai-Timern auf dem Pi`
            : "Überwachung der systemd-Timer und Hintergrund-Cronjobs auf dem Pi"
        }
        right={
          health ? (
            <Badge tone={tone} dot>
              <Activity size={10} />
              {getTimerHealthLabel(health)}
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
          {!countsAreCurrent && (
            <div className="rounded-sm border border-warn/30 bg-warn/5 p-2.5 flex items-start gap-2 text-2xs text-warn">
              <Clock size={13} className="shrink-0 mt-px" />
              <span>
                Diese Messung beschreibt nicht den aktuellen Zustand
                {health.status_reason ? ` (${health.status_reason})` : ""}. Die Zahlen unten sind
                der <strong>zuletzt bekannte</strong> Stand, kein Status.
              </span>
            </div>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div className="p-2.5 bg-bg-2 rounded-sm border border-line-subtle">
              <div className="text-2xs text-fg-subtle uppercase tracking-wide">Status</div>
              <div
                className={cn(
                  "text-xs font-semibold mt-1 uppercase",
                  tone === "pos"
                    ? "text-pos"
                    : tone === "warn"
                      ? "text-warn"
                      : tone === "neg"
                        ? "text-neg"
                        : "text-fg-subtle",
                )}
              >
                {getTimerHealthLabel(health)}
              </div>
            </div>
            <CountTile
              label="Überwacht"
              value={health.total}
              lastKnown={health.last_known_total}
              isCurrent={countsAreCurrent}
            />
            <CountTile
              label="Aktiv"
              value={health.active}
              lastKnown={health.last_known_active}
              isCurrent={countsAreCurrent}
              tone="pos"
            />
            <CountTile
              label="Ausgefallen"
              value={criticalCount}
              lastKnown={null}
              isCurrent={countsAreCurrent}
              tone={criticalCount > 0 ? "neg" : "fg"}
            />
          </div>

          {countsAreCurrent && criticalEntries.length > 0 && (
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

          {countsAreCurrent && expectedEntries.length > 0 && (
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
              <span>Alle überwachten Timer laufen ordnungsgemäß auf dem Pi 5.</span>
            </div>
          )}

          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between text-2xs text-fg-subtle border-t border-line-subtle pt-2 gap-2">
            <span>
              Letzte Messung: {health.checked_at ? formatAbsolute(health.checked_at) : "Keine"}
              {health.checked_at && ` (${formatRelative(health.checked_at)})`}
            </span>
            <span className="shrink-0">
              {/* §12: the budget is stated, not implied — and it comes from the
                  producer unit's own cadence rather than a hard-coded constant. */}
              Frische-Budget: {formatBudget(health.freshness?.stale_after_seconds)}
              {health.freshness?.producer_unit ? ` · ${health.freshness.producer_unit}` : ""}
            </span>
          </div>
        </div>
      )}
    </Card>
  );
}
