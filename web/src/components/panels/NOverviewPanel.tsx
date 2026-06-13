import { Target, Info, ChevronRight, Gauge } from "lucide-react";
import { Card, CardHeader, Badge, ProgressBar } from "@/components/ui/Primitives";
import { type NOverview, type NOverviewGate, type NOverviewEntry } from "@/lib/api";
import { useNOverview } from "@/lib/useNOverview";
import { cn } from "@/lib/utils";

/** Tonalität des Gate-Fortschritts (pure, getestet).
 *  muted = noch keine Zahl; neg < 50 %; warn 50–99 %; pos ab Schwelle. */
export function nGateTone(
  ratioPct: number | null,
  sufficient: boolean,
): "pos" | "warn" | "neg" | "muted" {
  if (ratioPct == null) return "muted";
  if (sufficient) return "pos";
  if (ratioPct >= 50) return "warn";
  return "neg";
}

function fmt(value: number | null): string {
  return value == null ? "—" : value.toLocaleString("de-DE");
}

const TONE_TEXT: Record<string, string> = {
  pos: "text-pos",
  warn: "text-warn",
  neg: "text-neg",
  muted: "text-fg-muted",
};

function GateBlock({
  gate,
  badgeLabel,
  icon,
  sublabel,
}: {
  gate: NOverviewGate;
  badgeLabel: string;
  icon: React.ReactNode;
  sublabel: string;
}) {
  const tone = nGateTone(gate.ratio_pct, gate.sufficient);
  const toneText = TONE_TEXT[tone];

  return (
    <div className="rounded-sm border border-info/25 bg-info/5 p-4">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <Badge tone="info" dot>
          {icon}
          {badgeLabel}
        </Badge>
        <span className="text-2xs text-fg-subtle">{sublabel}</span>
        {gate.verdict && (
          <Badge tone={gate.sufficient ? "pos" : "warn"} className="ml-auto">
            {gate.verdict}
          </Badge>
        )}
      </div>

      <div className="flex items-baseline gap-2">
        <span className={cn("text-3xl font-mono font-semibold tabular-nums", toneText)}>
          {fmt(gate.value)}
        </span>
        <span className="text-lg font-mono text-fg-subtle">/ {gate.threshold}</span>
        {gate.ratio_pct != null && (
          <span className={cn("text-xs font-mono", toneText)}>{gate.ratio_pct.toFixed(0)} %</span>
        )}
        {typeof gate.ev_after_costs_bps === "number" && (
          <span
            className={cn(
              "text-xs font-mono ml-1",
              gate.ev_after_costs_bps >= 0 ? "text-pos" : "text-neg",
            )}
          >
            EV {gate.ev_after_costs_bps.toFixed(1)} bps
          </span>
        )}
      </div>

      <div className="mt-2">
        <ProgressBar
          value={gate.value}
          target={gate.threshold}
          label={gate.label}
          sufficientSample={gate.value != null}
          size="md"
        />
      </div>

      <div className="mt-2 flex flex-col gap-1 text-2xs text-fg-muted">
        <span>
          <span className="font-mono text-fg">{gate.label}</span>
          {gate.filter ? ` · ${gate.filter}` : ""} · {gate.source}
        </span>
        <span>{gate.measures}</span>
        <span className="text-info font-medium">→ {gate.watch_hint}</span>
      </div>
    </div>
  );
}

function OtherRow({ entry }: { entry: NOverviewEntry }) {
  const tone = entry.status_tone ?? "muted";
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="w-14 shrink-0 text-right font-mono text-base tabular-nums text-fg-muted">
        {fmt(entry.value)}
      </span>
      <ChevronRight size={12} className="shrink-0 text-fg-subtle" />
      <div className="min-w-0 flex-1">
        <span className="text-xs text-fg">{entry.label}</span>
        <span className="text-2xs text-fg-subtle"> · {entry.measures}</span>
      </div>
      {entry.status_tag && (
        <Badge tone={tone === "neg" ? "neg" : tone} className="shrink-0">
          {entry.status_tag}
        </Badge>
      )}
    </div>
  );
}

export function NOverviewPanel() {
  const state = useNOverview();
  const data: NOverview | null = state.state === "ready" ? state.data : null;

  return (
    <Card padded>
      <CardHeader
        title={"Die 5 „n“ — was zählt fürs Edge-Gate?"}
        subtitle={
          "Zwei offene Gates steuern den Edge-Beweis. Die anderen Zähler sind Kontext (Diagnose / News / erreicht) — kein offenes Trading-Gate."
        }
        right={
          <Badge tone="info" title={data?.trap_note}>
            <Info size={10} />
            UX-Falle erklärt
          </Badge>
        }
      />

      {state.state === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">Lade n-Übersicht …</div>
      )}

      {state.state === "error" && (
        <div className="py-3 text-xs text-neg">
          <div className="font-semibold">n-Übersicht unerreichbar</div>
          <div className="text-fg-muted mt-1">
            {state.error.kind} · {state.error.message}
          </div>
          <div className="text-2xs text-fg-subtle mt-1 font-mono">GET /dashboard/api/n-overview</div>
        </div>
      )}

      {data && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <GateBlock
              gate={data.gate}
              badgeLabel="#167 EDGE-GATE"
              icon={<Target size={10} />}
              sublabel="IC/Brier-Sample"
            />
            <GateBlock
              gate={data.ev_gate}
              badgeLabel="EV-VERDICT"
              icon={<Gauge size={10} />}
              sublabel="ausgeführte Generator-Trades"
            />
          </div>

          <div className="flex items-center gap-2 pt-1">
            <span className="text-2xs uppercase tracking-wide text-fg-subtle">
              Kontext · kein offenes Trading-Gate
            </span>
            <span className="h-px flex-1 bg-line" />
          </div>

          <div className="divide-y divide-line-subtle">
            {data.others.map((o) => (
              <OtherRow key={o.key} entry={o} />
            ))}
          </div>

          <p className="text-2xs text-fg-subtle leading-relaxed pt-1">{data.trap_note}</p>
        </div>
      )}
    </Card>
  );
}
