// @data-source: /dashboard/api/quality
//
// Truth-Layer-Status-KPI (#314, Truth-Layer-Slice / Konzept §8/§9). Ehrlich gegen
// den bereits gepollten quality-Vertrag (KEIN neuer Endpoint): wie viele kanonische
// metric_contract-Metriken auflösen vs. gesamt, plus Contract-Version. ok = alle
// aufgelöst · degraded = welche fehlen · no_contract = kein Vertrag (unverifiziert,
// nicht „ok"). Kein Fake. quality wird als Prop durchgereicht (Dashboard pollt es
// ohnehin) — kein redundanter Fetch.
import { Card, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { deriveTruthLayer, truthLayerStateToStatus } from "@/lib/truthLayerStatus";
import type { DashboardQuality } from "@/lib/api";

export function TruthLayerKpi({
  quality,
  qualityState,
}: {
  quality: DashboardQuality | null;
  qualityState: "loading" | "ready" | "error";
}) {
  const s = deriveTruthLayer(quality);

  return (
    <Card padded>
      <div className="text-2xs uppercase tracking-wider text-fg-muted">Truth-Layer</div>
      <div className="mt-1.5 flex items-center gap-2">
        {qualityState === "error" ? (
          <StatusPill kind="critical" label="Endpoint-Fehler" />
        ) : quality == null ? (
          <StatusPill kind="pending" label="lädt" />
        ) : s.state === "no_contract" ? (
          <Badge tone="muted" dot title="Kein metric_contract im quality-Endpoint.">
            kein Vertrag
          </Badge>
        ) : (
          <StatusPill
            kind={truthLayerStateToStatus(s.state)}
            label={s.state === "ok" ? "konsistent" : "degradiert"}
          />
        )}
      </div>
      <div className="mt-1 text-2xs text-fg-subtle">
        {quality == null ? null : s.state === "no_contract" ? (
          <span>kein metric_contract aufgelöst</span>
        ) : (
          <span className="font-mono">
            {s.version != null ? `Contract v${s.version} · ` : ""}
            {s.resolved}/{s.total} Metriken aufgelöst
          </span>
        )}
      </div>
    </Card>
  );
}
