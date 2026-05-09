import { Card, CardHeader, Badge, ProgressBar } from "@/components/ui/Primitives";
import type { DashboardQuality } from "@/lib/api";
import { cn } from "@/lib/utils";
import { sourceLabel } from "@/lib/sourceLabels";

// V-DB4e 2026-05-08: Per-source 30-day rolling stability tile.
// Quelle: /dashboard/api/quality.per_source_stability (Backend-Field neu
// freigegeben durch dashboard.py:V-DB4e). Drift-Detection ueber 3 Windows
// a 30 Tagen — Operator sieht ob eine Source historisch stabil performt
// oder ob der Wilson-Lower zwischen Windows kippt.
//
// Operator-Folge 2026-05-08:
//   - Empty-State mit Erklaer-Box statt unsichtbarem Subtitle.
//   - Konsistente Visualisierung: Tone-Dot + ProgressBar (size="md") plus
//     das Drift-Mosaik rechts. Bar zeigt die jüngste Window-Hit-Rate,
//     Mosaik zeigt die Drift-Historie. Konsistent zur ActivePrecisionCard.

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

  if (!pss || Object.keys(pss.by_source).length === 0) {
    return (
      <Card padded>
        <CardHeader
          title="Source-Stability (rolling)"
          subtitle="Liefert eine Quelle ueber drei 30-Tage-Fenster konstant Treffer — oder driftet sie?"
        />
        <div className="rounded-md border border-line-subtle bg-bg-2 px-3 py-4 text-xs text-fg-muted leading-relaxed">
          <div className="font-medium text-fg mb-1">Stability-Daten kommen mit dem nächsten Hold-Report.</div>
          <p>
            Sobald drei vollständige 30-Tage-Fenster aufgeloest sind, erscheint hier pro Quelle
            die juengste Hit-Rate als Bar plus drei Drift-Kacheln (alt → neu) als Verlauf.
          </p>
        </div>
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
            {stableCount}/{sources.length} stabil
          </Badge>
        }
      />
      <div className="space-y-2.5">
        {sources.map(([source, m]) => {
          // Juengstes Window steht laut Backend an Index 0 (sorted desc by window_end).
          const latest = m.windows[0];
          const latestTone = latest ? windowTone(latest) : "muted";
          const dotClass =
            latestTone === "pos"
              ? "bg-pos"
              : latestTone === "warn"
                ? "bg-warn"
                : latestTone === "neg"
                  ? "bg-neg"
                  : "bg-fg-subtle/50";
          const barTone: "pos" | "warn" | "neg" | "muted" =
            latestTone === "pos"
              ? "pos"
              : latestTone === "warn"
                ? "warn"
                : latestTone === "neg"
                  ? "neg"
                  : "muted";
          return (
            <div key={source} className="grid items-center gap-3" style={{ gridTemplateColumns: "minmax(0,1fr) auto auto" }}>
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-xs">
                  {/* Tone-Dot konsistent zu ActivePrecisionCard. */}
                  <span
                    className={cn("inline-block h-2 w-2 rounded-full shrink-0", dotClass)}
                    aria-hidden="true"
                  />
                  <span className="text-fg font-medium truncate" title={sourceLabel(source).hint}>
                    {sourceLabel(source).label}
                  </span>
                  {sourceLabel(source).label !== source && (
                    <span
                      className="text-2xs font-mono text-fg-subtle shrink-0 truncate"
                      title={`Backend-Source-Key: ${source}`}
                    >
                      {source}
                    </span>
                  )}
                  {latest && (
                    <span className="text-2xs font-mono text-fg-subtle shrink-0 ml-auto">
                      {latest.hit_rate_pct != null ? `${latest.hit_rate_pct.toFixed(1)}%` : "—"}
                      <span className="text-fg-subtle/70 ml-1">n={latest.resolved}</span>
                    </span>
                  )}
                </div>
                <div className="relative mt-1.5">
                  <ProgressBar
                    value={latest?.hit_rate_pct ?? null}
                    target={100}
                    tone={barTone}
                    size="md"
                    sufficientSample={latest?.n_threshold_met ?? false}
                    label={`${source} juengste Hit-Rate`}
                  />
                  {/* Wilson-Lower-Threshold-Marker konsistent zu ActivePrecisionCard. */}
                  <span
                    className="absolute top-0 bottom-0 w-px bg-fg-subtle/35 pointer-events-none"
                    style={{ left: `${pss.min_wilson_low_pct}%` }}
                    title={`Stability-Gate liegt bei ${pss.min_wilson_low_pct.toFixed(0)}% Wilson-Lower`}
                    aria-hidden="true"
                  />
                </div>
              </div>
              {/* Drift-Mosaik: 3 Window-Kacheln (alt → neu rechts). */}
              <div className="flex gap-1 shrink-0" aria-label={`${source} Drift-Verlauf`}>
                {[...m.windows].reverse().map((w, idx) => {
                  const tone = windowTone(w);
                  return (
                    <div
                      key={idx}
                      title={windowTitle(w)}
                      className={cn(
                        "h-5 w-9 rounded-xs border text-[10px] flex items-center justify-center font-mono",
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
                className={cn(
                  "shrink-0 w-[60px] justify-center",
                  // Drift-Source atmet permanent — bleibt im Operator-Blickfeld.
                  !m.stable && "attention-breathe-warn",
                )}
              >
                {m.stable ? "stabil" : "Drift"}
              </Badge>
            </div>
          );
        })}
      </div>
      <div className="mt-4 pt-3 border-t border-line-subtle text-2xs text-fg-muted leading-relaxed">
        Bar = juengste 30-Tage-Hit-Rate · Mosaik = drei Buckets von links (alt) nach rechts (neu, ~aktuell).
        Eine Source ist <span className="text-pos">stabil</span>, wenn jeder Bucket mit Daten
        n≥{pss.min_resolved_per_window} UND Wilson-Lower ≥{pss.min_wilson_low_pct.toFixed(0)}% erfuellt.
        Hover ueber eine Kachel fuer Details.
      </div>
    </Card>
  );
}
