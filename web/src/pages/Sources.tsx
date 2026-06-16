// @data-source: /dashboard/api/quality (per_source_active_precision · source_reliability · per_source_stability)
//
// Quellen-Seite (UI-Update 2026.06, WP-2.1 / Konzept §6). Eigene IA-Seite für
// Quellen-Qualität: Top-5/Flop-5-Rangliste (echte Trefferquoten, nur bei
// ausreichender Stichprobe) plus die bestehenden Source-Panels (Reliability,
// Per-Source-Precision, rollende Stabilität). Nur echte Daten — bei Lücke
// degradieren die Panels ehrlich.
import { PageHeader } from "@/layout/PageHeader";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { PanelErrorBoundary } from "@/components/PanelErrorBoundary";
import { SourceReliabilityPanel } from "@/components/panels/SourceReliabilityPanel";
import { PerSourcePrecisionPanel } from "@/components/panels/PerSourcePrecisionPanel";
import { PerSourceStabilityPanel } from "@/components/panels/PerSourceStabilityPanel";
import { useDashboardQuality } from "@/lib/useDashboardQuality";
import { topFlopSources, type SourceRank } from "@/lib/sourceRanking";
import { cn } from "@/lib/utils";

function RankRow({ rank, s, kind }: { rank: number; s: SourceRank; kind: "top" | "flop" }) {
  return (
    <li className="flex items-center gap-2 py-1">
      <span className="w-5 shrink-0 text-right font-mono text-2xs text-fg-subtle">{rank}</span>
      <span className="min-w-0 flex-1 truncate text-xs text-fg">{s.label}</span>
      <span className="font-mono text-2xs text-fg-subtle">{s.resolved}n</span>
      <span
        className={cn(
          "w-14 text-right font-mono text-sm font-semibold",
          kind === "top" ? "text-pos" : "text-neg",
        )}
      >
        {s.hitRate.toFixed(1)}%
      </span>
      <StatusPill kind={s.passesGate ? "verified" : "unverified"} showIcon={false} dot label={s.passesGate ? "Gate" : "—"} />
    </li>
  );
}

export function SourcesPage() {
  const q = useDashboardQuality();
  const data = q.state === "ready" ? q.data : null;
  const { top, flop } = topFlopSources(data?.per_source_active_precision?.by_source);

  return (
    <div className="p-4 xl:p-5 space-y-4 max-w-[1680px] mx-auto">
      <PageHeader title="Quellen" sub="Welche Quellen liefern gute Signale — und welche brauchen einen Fix." />

      <Card padded>
        <CardHeader
          title="Top-5 / Flop-5 nach Trefferquote"
          subtitle="Nur Quellen mit ausreichender Stichprobe (Gate-n) — kein Ranking auf 1–2 Resolves."
          right={
            top.length === 0 ? (
              <Badge tone="muted" dot>
                keine wertbaren Quellen
              </Badge>
            ) : null
          }
        />
        {top.length === 0 ? (
          <div className="py-2 text-xs text-fg-muted">
            {q.state === "error"
              ? "Quality-Endpoint unerreichbar."
              : "Noch keine Quelle mit ausreichender Stichprobe für ein belastbares Ranking."}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-pos">Top — beste Quellen</div>
              <ul className="divide-y divide-line-subtle">
                {top.map((s, i) => (
                  <RankRow key={s.name} rank={i + 1} s={s} kind="top" />
                ))}
              </ul>
            </div>
            <div>
              <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-neg">Flop — problematische Quellen</div>
              {flop.length === 0 ? (
                <div className="py-1 text-2xs text-fg-subtle">Zu wenige wertbare Quellen für eine Flop-Liste.</div>
              ) : (
                <ul className="divide-y divide-line-subtle">
                  {flop.map((s, i) => (
                    <RankRow key={s.name} rank={i + 1} s={s} kind="flop" />
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </Card>

      <PanelErrorBoundary name="Source-Reliability">
        <SourceReliabilityPanel data={data} />
      </PanelErrorBoundary>
      <PanelErrorBoundary name="Per-Source-Precision">
        <PerSourcePrecisionPanel data={data} />
      </PanelErrorBoundary>
      <PanelErrorBoundary name="Per-Source-Stability">
        <PerSourceStabilityPanel data={data} />
      </PanelErrorBoundary>
    </div>
  );
}
