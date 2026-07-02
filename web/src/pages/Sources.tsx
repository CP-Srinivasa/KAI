// @data-source: /dashboard/api/source-lifecycle (Quellen-Güte-Rangliste — SSOT „wer top/flop")
//               /dashboard/api/quality (per_source_active_precision · source_reliability · per_source_stability)
//
// Quellen-Seite (Phase 0 / DALI-P-201). EINE kanonische Güte-Rangliste statt drei
// widersprüchlicher Ranking-Sichten: SourceLifecyclePanel („Quellen-Güte") ist die
// SSOT für „wer liefert gut/schlecht". Darunter die Detail-Diagnostik (Reliability,
// Precision, Stability). Nur echte Daten — bei Lücke degradieren die Panels ehrlich.
import { PageHeader } from "@/layout/PageHeader";
import { SectionLabel } from "@/components/ui/Primitives";
import { PanelErrorBoundary } from "@/components/PanelErrorBoundary";
import { SourceReliabilityPanel } from "@/components/panels/SourceReliabilityPanel";
import { PerSourcePrecisionPanel } from "@/components/panels/PerSourcePrecisionPanel";
import { PerSourceStabilityPanel } from "@/components/panels/PerSourceStabilityPanel";
import { SourceActivityPanel } from "@/components/panels/SourceActivityPanel";
import { SourceLifecyclePanel } from "@/components/panels/SourceLifecyclePanel";
import { SourceDiscoveryPanel } from "@/components/panels/SourceDiscoveryPanel";
import { useDashboardQuality } from "@/lib/useDashboardQuality";
import { LiveDot } from "@/components/ui/LiveDot";
import { liveDotProps } from "@/lib/freshness";

export function SourcesPage() {
  const q = useDashboardQuality();
  const data = q.state === "ready" ? q.data : null;

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

      <PanelErrorBoundary name="Quellen-Güte">
        <SourceLifecyclePanel />
      </PanelErrorBoundary>

      <PanelErrorBoundary name="Quellen-Discovery">
        <SourceDiscoveryPanel />
      </PanelErrorBoundary>

      <SectionLabel className="pt-2">Detail-Diagnostik</SectionLabel>

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
