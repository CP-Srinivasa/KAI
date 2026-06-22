// @data-source: /dashboard/api/quality (per_source_active_precision · source_reliability · per_source_stability)
//
// Quellen-Seite (UI-Update 2026.06, WP-2.1 / Konzept §6). Eigene IA-Seite für
// Quellen-Qualität: Top-5/Flop-5-Rangliste (echte Trefferquoten, nur bei
// ausreichender Stichprobe) plus die bestehenden Source-Panels (Reliability,
// Per-Source-Precision, rollende Stabilität). Nur echte Daten — bei Lücke
// degradieren die Panels ehrlich.
import { PageHeader } from "@/layout/PageHeader";
import { Card, CardHeader, Badge, InfoHint, ProgressBar } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { PanelErrorBoundary } from "@/components/PanelErrorBoundary";
import { SourceReliabilityPanel } from "@/components/panels/SourceReliabilityPanel";
import { PerSourcePrecisionPanel } from "@/components/panels/PerSourcePrecisionPanel";
import { PerSourceStabilityPanel } from "@/components/panels/PerSourceStabilityPanel";
import { SourceActivityPanel } from "@/components/panels/SourceActivityPanel";
import { useDashboardQuality } from "@/lib/useDashboardQuality";
import { useDashboardProvenance } from "@/lib/useDashboardProvenance";
import { LiveDot } from "@/components/ui/LiveDot";
import { liveDotProps } from "@/lib/freshness";
import { topFlopSources, rankProvenanceSources, type SourceRank } from "@/lib/sourceRanking";
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

  // Zweite Trefferquoten-Sicht aus der Provenance (andere SSOT: getaggter
  // Signalfluss). Konvergieren beide Sichten, ist die Quelle belastbar.
  const p = useDashboardProvenance();
  const prov = p.state === "ready" ? p.data : null;
  const provRanked = rankProvenanceSources(prov?.by_source);
  const provMaxN = provRanked.length ? Math.max(...provRanked.map((s) => s.resolved)) : 0;

  return (
    <div className="p-4 xl:p-5 space-y-4 max-w-[1680px] mx-auto">
      <PageHeader
        title="Quellen"
        sub="Welche Quellen liefern gute Signale — und welche brauchen einen Fix."
        right={<LiveDot {...liveDotProps(q, q.data?.generated_at)} staleAfterMs={75_000} />}
      />

      <PanelErrorBoundary name="Source-Activity">
        <SourceActivityPanel />
      </PanelErrorBoundary>

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

      {provRanked.length > 0 && (
        <Card padded>
          <CardHeader
            title="Trefferquote pro Quelle"
            subtitle="Zweite Sicht aus der Provenance (getaggter Signalfluss) — unabhängig vom Ranking oben."
            right={
              <div className="flex items-center gap-2">
                {prov?.verdict ? <Badge tone="info">{prov.verdict}</Badge> : null}
                <InfoHint
                  label="Warum eine zweite Sicht?"
                  hint="Das Ranking oben nutzt per_source_active_precision (Gate-gefiltert). Diese Balken kommen aus /dashboard/api/provenance by_source (resolved/hits ohne unknown-Bucket) — eine andere SSOT. Konvergieren beide, ist die Quelle belastbar; divergieren sie, lohnt ein Blick. Dünne Stichproben sind gedämpft, nicht versteckt."
                />
              </div>
            }
          />
          <div className="mt-2 space-y-1.5">
            {provRanked.map((s) => (
              <div key={s.name} className="flex items-center gap-3">
                <span className="w-28 shrink-0 truncate text-xs text-fg">{s.label}</span>
                <div className="min-w-0 flex-1">
                  <ProgressBar
                    value={s.hitRate ?? 0}
                    target={100}
                    label={`${s.label} Trefferquote`}
                    sufficientSample={s.sufficient}
                  />
                </div>
                <span className="w-12 shrink-0 text-right font-mono tabular-nums text-xs text-fg">
                  {s.hitRate == null ? "—" : `${s.hitRate.toFixed(0)}%`}
                </span>
                <span className="w-16 shrink-0 text-right font-mono text-2xs text-fg-subtle">
                  {s.resolved}/{provMaxN} n
                </span>
                {!s.sufficient && (
                  <StatusPill kind="unverified" label="dünn" dot={false} showIcon={false} />
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

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
