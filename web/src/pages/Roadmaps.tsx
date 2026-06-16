// @data-source: none (deklarativer Roadmap-Snapshot — kein Backend-Endpoint)
//
// Roadmaps (UI-Update 2026.06, WP-2.2 / Konzept §19). Macht die laufenden
// Roadmaps als Planungs-/Fortschritts-Struktur sichtbar: Phasen-Timeline +
// Status je Phase. EHRLICH: deklarativer Snapshot (Stand sichtbar), KEINE
// live-berechneten Metriken — es gibt kein Roadmap-Backend.
import { PageHeader } from "@/layout/PageHeader";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { TimelineRail } from "@/components/viz/TimelineRail";
import {
  ROADMAPS,
  ROADMAP_SNAPSHOT_DATE,
  phaseStatusKind,
  phaseStatusTone,
  type Roadmap,
} from "@/lib/roadmaps";

function RoadmapCard({ rm }: { rm: Roadmap }) {
  return (
    <Card padded>
      <CardHeader title={rm.title} subtitle={rm.subtitle} />
      <TimelineRail
        className="mb-3"
        items={rm.phases.map((p) => ({ key: p.id, label: p.label, tone: phaseStatusTone(p.status) }))}
      />
      <ul className="space-y-1.5">
        {rm.phases.map((p) => (
          <li key={p.id} className="flex items-start gap-2 rounded-sm border border-line-subtle bg-bg-1 px-2.5 py-2">
            <StatusPill kind={phaseStatusKind(p.status)} showIcon={false} dot label={p.status} />
            <div className="min-w-0 flex-1">
              <div className="text-xs font-semibold text-fg">{p.label}</div>
              {p.note && <p className="mt-0.5 text-2xs leading-relaxed text-fg-subtle">{p.note}</p>}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function RoadmapsPage() {
  return (
    <div className="p-4 xl:p-5 space-y-4 max-w-[1680px] mx-auto">
      <PageHeader
        title="Roadmaps"
        sub="Welche Phase ist aktiv, abgeschlossen, geplant oder gated — auf einen Blick."
        right={
          <Badge tone="muted" title="Deklarativer Snapshot — kein Roadmap-Backend, Stand manuell gepflegt.">
            Snapshot · {ROADMAP_SNAPSHOT_DATE}
          </Badge>
        }
      />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {ROADMAPS.map((rm) => (
          <RoadmapCard key={rm.id} rm={rm} />
        ))}
      </div>
    </div>
  );
}
