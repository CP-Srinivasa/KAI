import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import type { DashboardQuality } from "@/lib/api";
import { cn } from "@/lib/utils";

// V-DB4e 2026-05-08: Per-source 30-day rolling stability tile.
// Quelle: /dashboard/api/quality.per_source_stability (Backend-Field neu
// freigegeben durch dashboard.py:V-DB4e). Drift-Detection ueber 3 Windows
// a 30 Tagen — Operator sieht ob eine Source historisch stabil performt
// oder ob der Wilson-Lower zwischen Windows kippt.

type Window = NonNullable<
  DashboardQuality["per_source_stability"]
>["by_source"][string]["windows"][number];

function windowTone(w: Window): "pos" | "warn" | "neg" | "muted" {
  if (!w.n_threshold_met) return "muted";
  if (w.passes_window) return "pos";
  if (w.wilson_low_threshold_met) return "warn";
  return "neg";
}

function windowTitle(w: Window): string {
  const start = w.window_start.substring(0, 10);
  const end = w.window_end.substring(0, 10);
  return `${start} → ${end} · n=${w.resolved} · ${
    w.hit_rate_pct != null ? `${w.hit_rate_pct.toFixed(1)}%` : "—"
  } · CI-Lower ${w.ci_low_pct != null ? `${w.ci_low_pct.toFixed(1)}%` : "—"}${
    w.fail_reason ? ` · ${w.fail_reason}` : ""
  }`;
}

export function PerSourceStabilityPanel({ data }: { data: DashboardQuality | null }) {
  const pss = data?.per_source_stability;
  if (!pss) {
    return (
      <Card padded>
        <CardHeader
          title="Source-Stability"
          subtitle="Stability-Daten kommen mit dem nächsten Hold-Report."
        />
        <div className="text-2xs text-fg-subtle">noch keine Daten</div>
      </Card>
    );
  }

  const sources = Object.entries(pss.by_source).sort((a, b) => {
    if (a[1].stable !== b[1].stable) return a[1].stable ? -1 : 1;
    return a[0].localeCompare(b[0]);
  });

  const stableCount = sources.filter(([, m]) => m.stable).length;

  return (
    <Card padded>
      <CardHeader
        title="Source-Stability (rolling)"
        subtitle={`${pss.window_count} Windows × ${pss.window_days}d · min n=${pss.min_resolved_per_window} · Wilson-Lower ≥${pss.min_wilson_low_pct.toFixed(0)}%`}
        right={
          <Badge tone={stableCount === sources.length ? "pos" : stableCount > 0 ? "warn" : "muted"} dot>
            {stableCount}/{sources.length} stable
          </Badge>
        }
      />
      <div className="space-y-2">
        {sources.map(([source, m]) => (
          <div key={source} className="flex items-center justify-between gap-3 text-xs">
            <span className="text-fg font-medium truncate min-w-0 flex-1">{source}</span>
            <div className="flex gap-1 shrink-0">
              {m.windows.map((w, idx) => {
                const tone = windowTone(w);
                return (
                  <div
                    key={idx}
                    title={windowTitle(w)}
                    className={cn(
                      "h-5 w-10 rounded-xs border text-[10px] flex items-center justify-center font-mono",
                      tone === "pos" && "bg-pos/15 text-pos border-pos/30",
                      tone === "warn" && "bg-warn/15 text-warn border-warn/30",
                      tone === "neg" && "bg-neg/15 text-neg border-neg/30",
                      tone === "muted" && "bg-bg-3 text-fg-subtle border-line-subtle",
                    )}
                  >
                    {w.resolved > 0 ? `n${w.resolved}` : "—"}
                  </div>
                );
              })}
            </div>
            <Badge
              tone={m.stable ? "pos" : "muted"}
              className="shrink-0 w-[60px] justify-center"
            >
              {m.stable ? "stable" : "drift"}
            </Badge>
          </div>
        ))}
      </div>
      {sources.length === 0 && (
        <div className="text-2xs text-fg-subtle">keine Source mit Stability-Daten</div>
      )}
      <div className="mt-4 pt-3 border-t border-line-subtle text-2xs text-fg-muted leading-relaxed">
        Drei 30-Tage-Buckets von rechts (alt) nach links (neu, ~aktuell). Hover → Details. Eine Source ist <span className="text-pos">stable</span>,
        wenn jeder Bucket mit Daten n≥{pss.min_resolved_per_window} UND Wilson-Lower ≥{pss.min_wilson_low_pct.toFixed(0)}% erfüllt.
      </div>
    </Card>
  );
}
