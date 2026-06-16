// @data-source: /dashboard/api/quality + /dashboard/api/provenance
import { Radio, TrendingUp, TrendingDown, Wrench } from "lucide-react";
import { PageHeader } from "@/layout/PageHeader";
import { Card, CardHeader, Badge, SectionLabel, InfoHint, ProgressBar } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { PanelErrorBoundary } from "@/components/PanelErrorBoundary";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { SourceReliabilityPanel } from "@/components/panels/SourceReliabilityPanel";
import { PerSourcePrecisionPanel } from "@/components/panels/PerSourcePrecisionPanel";
import { PerSourceStabilityPanel } from "@/components/panels/PerSourceStabilityPanel";
import { useDashboardQuality } from "@/lib/useDashboardQuality";
import { useDashboardProvenance } from "@/lib/useDashboardProvenance";
import { sourceLabel } from "@/lib/sourceLabels";
import type { DashboardQuality, ProvenanceMetrics } from "@/lib/api";
import type { StatusKind } from "@/lib/status";
import { cn } from "@/lib/utils";

// Quellen — "Welche Quelle trägt, welche ist kaputt?" (Konzept §6 + §20).
// Reuse-first: die belastbaren Per-Source-Panels (Reliability/Precision/Stability)
// werden komponiert, nicht neu gebaut. Eigener Mehrwert oben: Top/Flop-Ranking +
// Trefferquote-pro-Quelle aus echten Daten. Was es noch nicht als Datenpfad gibt
// (Live-Ingestion-Zyklus, Quelle×Tag-Heatmap, Duplikat-Pareto) ist ehrlich als
// Phase-2-Roadmap markiert — KEINE Fake-Werte.

const TRUSTED_N = 20;
const PROVISIONAL_N = 5;

type SourceScore = NonNullable<DashboardQuality["source_reliability"]>["top_sources"][number];

// tier → Status-Sprache-SSOT (eine Bedeutung app-weit).
function tierKind(tier: string): StatusKind {
  if (tier === "trusted") return "verified";
  if (tier === "watch") return "degraded";
  if (tier === "low") return "rejected";
  return "unverified"; // insufficient / unbekannt
}

function whyGood(s: SourceScore): string {
  if (s.n >= TRUSTED_N) return `belastbar (n=${s.n}) · trägt`;
  return `vorläufig stark (n=${s.n})`;
}

function whyBad(s: SourceScore): string {
  if (s.n < PROVISIONAL_N) return `zu dünn (n=${s.n}) → mehr Daten sammeln`;
  if (s.tier === "low") return `niedrige Trefferquote → Quelle prüfen/abwerten`;
  if (s.tier === "insufficient") return `Stichprobe < ${TRUSTED_N} → unsicher`;
  return `Trefferquote unter Schnitt`;
}

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(0)}%`;
}

function RankRow({
  rank,
  name,
  kind,
  estimate,
  why,
  good,
}: {
  rank: number;
  name: string;
  kind: StatusKind;
  estimate: number | null;
  why: string;
  good: boolean;
}) {
  return (
    <div className="flex items-center gap-3 py-1.5 border-b border-line-subtle/60 last:border-0">
      <span className="w-5 shrink-0 text-2xs font-mono text-fg-subtle text-right">{rank}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm text-fg">{name}</span>
          <StatusPill kind={kind} dot={false} />
        </div>
        <div className="text-2xs text-fg-subtle mt-0.5">{why}</div>
      </div>
      <span
        className={cn(
          "shrink-0 font-mono tabular-nums text-sm",
          good ? "text-pos" : "text-warn",
        )}
      >
        {pct(estimate)}
      </span>
    </div>
  );
}

export function SourcesPage() {
  const q = useDashboardQuality();
  const quality = q.state === "ready" ? q.data : null;
  const p = useDashboardProvenance();
  const prov = p.state === "ready" ? p.data : null;

  const sr = quality?.source_reliability;
  const ranked = [...(sr?.top_sources ?? [])].sort(
    (a, b) => (b.wilson_lower_95_pct ?? -1) - (a.wilson_lower_95_pct ?? -1),
  );
  const top = ranked.slice(0, 5);
  const flop = [...ranked].reverse().slice(0, 5);

  // Trefferquote pro Quelle aus der Provenance (eigenständige Sicht, andere SSOT).
  const bySource: ProvenanceMetrics[] = [...(prov?.by_source ?? [])]
    .filter((s) => s.source && s.source !== "unknown")
    .sort((a, b) => (b.hit_rate_pct ?? -1) - (a.hit_rate_pct ?? -1));
  const provMax = bySource.length ? bySource[0].resolved : 0;

  const loading = q.state === "loading" || p.state === "loading";

  return (
    <div className="p-5 xl:p-6 space-y-6 max-w-[1680px] mx-auto">
      <PageHeader
        title="Quellen"
        sub="Welche Quelle trägt, welche ist kaputt?"
        tone="info"
        icon={<Radio size={18} />}
        right={
          sr ? (
            <div className="flex items-center gap-2">
              <Badge tone="info">{sr.source_count} Quellen</Badge>
              {sr.trusted_count != null && <Badge tone="pos">{sr.trusted_count} belastbar</Badge>}
            </div>
          ) : undefined
        }
      />

      {loading && (
        <Card padded className="py-6 text-center text-sm text-fg-subtle">
          Lade Quellen-Metriken …
        </Card>
      )}

      {q.state === "error" && (
        <Card padded className="border-neg/30 bg-neg/5 text-2xs font-mono text-neg">
          Quality-Endpoint nicht erreichbar · /dashboard/api/quality
        </Card>
      )}

      {/* Headline: Top / Flop — sofort sichtbar, was trägt und was nervt */}
      {sr && ranked.length > 0 && (
        <section className="grid gap-3 lg:grid-cols-2">
          <Card padded className="overflow-hidden">
            <CardHeader
              title={
                <span className="flex items-center gap-1.5">
                  <TrendingUp size={14} className="text-pos" /> Top 5 Quellen
                </span>
              }
              subtitle="höchste belastbare Trefferquote (Wilson-Untergrenze)"
              right={
                <InfoHint
                  label="Warum gut?"
                  hint="Sortiert nach der Wilson-95%-Untergrenze der Trefferquote — small-n-Quellen rutschen automatisch nach unten, damit nichts unverdient oben steht. Belastbar ab n≥20."
                />
              }
            />
            <div className="mt-1">
              {top.map((s, i) => (
                <RankRow
                  key={s.source_name}
                  rank={i + 1}
                  name={sourceLabel(s.source_name).label}
                  kind={tierKind(s.tier)}
                  estimate={s.point_estimate_pct}
                  why={whyGood(s)}
                  good
                />
              ))}
            </div>
          </Card>

          <Card padded className="overflow-hidden">
            <CardHeader
              title={
                <span className="flex items-center gap-1.5">
                  <TrendingDown size={14} className="text-warn" /> Flop 5 Quellen
                </span>
              }
              subtitle="niedrigste Trefferquote / dünnste Stichprobe — was fixen?"
              right={
                <InfoHint
                  label="Warum schlecht?"
                  hint="Schwächste Wilson-Untergrenze. Ursache steht je Zeile: zu dünne Stichprobe (mehr sammeln), niedrige Trefferquote (Quelle abwerten/prüfen) oder unsichere Stichprobe (< 20)."
                />
              }
            />
            <div className="mt-1">
              {flop.map((s, i) => (
                <RankRow
                  key={s.source_name}
                  rank={i + 1}
                  name={sourceLabel(s.source_name).label}
                  kind={tierKind(s.tier)}
                  estimate={s.point_estimate_pct}
                  why={whyBad(s)}
                  good={false}
                />
              ))}
            </div>
          </Card>
        </section>
      )}

      {/* Trefferquote pro Quelle — eigenständige Provenance-Sicht (Ranking-Bars) */}
      {bySource.length > 0 && (
        <Card padded>
          <CardHeader
            title="Trefferquote pro Quelle"
            subtitle={`aufgelöste Richtungs-Signale · n_total ${prov?.overall.resolved ?? 0}`}
            right={
              prov?.verdict ? <Badge tone="info">{prov.verdict}</Badge> : undefined
            }
          />
          <div className="mt-2 space-y-2">
            {bySource.map((s) => {
              const enough = s.sample_sufficient;
              return (
                <div key={s.source} className="flex items-center gap-3">
                  <span className="w-28 shrink-0 truncate text-xs text-fg">
                    {sourceLabel(s.source).label}
                  </span>
                  <div className="flex-1 min-w-0">
                    <ProgressBar
                      value={s.hit_rate_pct ?? 0}
                      target={100}
                      label={`${sourceLabel(s.source).label} Trefferquote`}
                      sufficientSample={enough}
                    />
                  </div>
                  <span className="w-12 shrink-0 text-right font-mono tabular-nums text-xs text-fg">
                    {pct(s.hit_rate_pct)}
                  </span>
                  <span className="w-16 shrink-0 text-right text-2xs font-mono text-fg-subtle">
                    {s.resolved}/{provMax} n
                  </span>
                  {!enough && (
                    <StatusPill kind="unverified" label="dünn" dot={false} showIcon={false} />
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* Reuse: die belastbaren Per-Source-Panels (eine SSOT, nicht dupliziert) */}
      {quality && (
        <section className="space-y-3">
          <SectionLabel>Quellen-Reliabilität · Präzision · Stabilität</SectionLabel>
          <div className="grid gap-3 xl:grid-cols-3">
            <PanelErrorBoundary name="Source-Reliability">
              <SourceReliabilityPanel data={quality} />
            </PanelErrorBoundary>
            <PanelErrorBoundary name="Source-Precision">
              <PerSourcePrecisionPanel data={quality} />
            </PanelErrorBoundary>
            <PanelErrorBoundary name="Source-Stability">
              <PerSourceStabilityPanel data={quality} />
            </PanelErrorBoundary>
          </div>
        </section>
      )}

      {/* Phase-2 — ehrliche Roadmap für fehlende Datenpfade, kein Fake */}
      <section className="space-y-3">
        <div className="flex items-center gap-1.5">
          <Wrench size={13} className="text-ai" />
          <SectionLabel>Phase-2 — fehlende Datenpfade</SectionLabel>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <PreparedPanel
            title="Live-Ingestion-Zyklus"
            reason="Welche Quelle läuft gerade durch, was liefert sie, wo steht der Verarbeitungsstatus? Ein Live-Streifen über den aktuellen Feed-Durchlauf."
            detail="Es gibt noch keinen Endpoint, der den laufenden Ingestion-Zyklus pro Quelle exponiert (running/last_seen/latency)."
            status="roadmap"
            roadmapNote="Phase-2: GET /sources/ingestion-cycle (running, last_seen, latency, items)."
          />
          <PreparedPanel
            title="Heatmap Quelle × Tag"
            reason="Wo bricht eine Quelle über die Zeit ein? Frische/Hit pro Quelle und Tag als Wärmekarte — Ausfälle und Drift auf einen Blick."
            detail="Die Reliability liefert aggregierte Fenster, aber keine Quelle×Tag-Matrix. Braucht ein zeitlich aufgelöstes Per-Source-Aggregat."
            status="roadmap"
            roadmapNote="Phase-2: per-source-per-day Aggregat → Heatmap-Viz (vorhanden)."
          />
          <PreparedPanel
            title="Duplikat-Pareto"
            reason="Welche Quelle erzeugt die meisten Duplikate? Pareto über Duplikat-Gründe, um den größten Lärm-Verursacher zuerst zu fixen."
            detail="Dedup passiert im External-Signals-Pfad, aber Duplikat-Gründe sind nicht pro Quelle aggregiert exponiert."
            status="roadmap"
            roadmapNote="Phase-2: Dedup-Reason-Aggregat pro Quelle → Pareto-Viz."
          />
        </div>
      </section>
    </div>
  );
}
