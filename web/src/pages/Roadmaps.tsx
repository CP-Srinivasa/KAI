// @data-source: /dashboard/api/operator-board (nur für prereg-gebundene Phasen)
//
// Roadmaps (UI-Update 2026.06, WP-2.2 / Konzept §19). Macht die laufenden
// Roadmaps als Planungs-/Fortschritts-Struktur sichtbar: Phasen-Timeline +
// Status je Phase.
//
// 2026-07-30 (Operator-Befund „Snapshot · 2026-07-04 ist absolut unakzeptabel"):
// der Badge zeigte nur ein Datum — ein 26 Tage alter Snapshot sah damit genauso
// aus wie ein frischer. Jetzt bewertet `roadmapFreshness` die OFFENEN Phasen
// (Chronik veraltet nicht, 12 von 15 sind `done`), und Phasen mit `prereg`
// holen ihren Zustand LIVE aus dem Prä-Reg-Ledger statt ihn zu behaupten.
import { PageHeader } from "@/layout/PageHeader";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { TimelineRail } from "@/components/viz/TimelineRail";
import { fetchOperatorBoard, type OperatorPrereg } from "@/lib/api";
import {
  ROADMAPS,
  ROADMAP_SNAPSHOT_DATE,
  phaseStatusKind,
  phaseStatusTone,
  roadmapFreshness,
  type Roadmap,
} from "@/lib/roadmaps";
import { useApi } from "@/lib/useApi";

const PREREG_LABEL: Record<OperatorPrereg["state"], string> = {
  judgeable: "urteilsfähig",
  eval_check: "Evaluator fällig",
  maturing: "reift",
  no_counter: "ungezählt",
};

/** Live-Zeile für eine prereg-gebundene Phase — offen, fällig oder aufgelöst. */
function PreregLive({ id, row }: { id: string; row: OperatorPrereg | undefined }) {
  if (!row) {
    // Nicht in den offenen Prä-Regs ⇒ terminal aufgelöst (MET/NOT_MET) oder
    // (noch) nicht registriert. Beides ist eine Aussage, keine Lücke.
    return (
      <p className="mt-0.5 text-2xs text-fg-subtle">
        live: <code>{id}</code> nicht mehr offen — Verdikt-Ledger prüfen.
      </p>
    );
  }
  return (
    <p
      className={`mt-0.5 text-2xs ${
        row.state === "judgeable" ? "text-neg" : row.state === "eval_check" ? "text-warn" : "text-info"
      }`}
    >
      live: {PREREG_LABEL[row.state]}
      {row.n_proxy !== null && row.n_target ? ` · n≈${row.n_proxy}/${row.n_target}` : ""} ·{" "}
      <code>{id}</code>
    </p>
  );
}

function RoadmapCard({ rm, preregs }: { rm: Roadmap; preregs: Map<string, OperatorPrereg> | null }) {
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
              {p.prereg && preregs && <PreregLive id={p.prereg} row={preregs.get(p.prereg)} />}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function RoadmapsPage() {
  const board = useApi(fetchOperatorBoard, 300_000);
  const preregs =
    board.state === "ready"
      ? new Map((board.data.live?.open_preregs ?? []).map((r) => [r.prereg_id, r]))
      : null;

  const allPhases = ROADMAPS.flatMap((rm) => rm.phases);
  const fresh = roadmapFreshness(allPhases, ROADMAP_SNAPSHOT_DATE);

  return (
    <div className="p-4 xl:p-5 space-y-4 max-w-[1680px] mx-auto">
      <PageHeader
        title="Roadmaps"
        sub="Welche Phase ist aktiv, abgeschlossen, geplant oder gated — auf einen Blick."
        right={
          <Badge
            tone={fresh.tone}
            dot={fresh.isStale}
            title={
              fresh.isStale
                ? "Offene Phasen wurden seit der Schwelle nicht durchgesehen — Notizen gegen die Ledger prüfen."
                : "Frische zählt nur die OFFENEN Phasen; abgeschlossene Phasen veralten nicht. Prä-Reg-gebundene Phasen sind live."
            }
          >
            {fresh.label}
          </Badge>
        }
      />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {ROADMAPS.map((rm) => (
          <RoadmapCard key={rm.id} rm={rm} preregs={preregs} />
        ))}
      </div>
    </div>
  );
}
