import { Target, Info, ChevronRight } from "lucide-react";
import { Card, CardHeader, Badge, ProgressBar } from "@/components/ui/Primitives";
import { type NOverview, type NOverviewGate } from "@/lib/api";
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

function GateBlock({ gate }: { gate: NOverviewGate }) {
  const tone = nGateTone(gate.ratio_pct, gate.sufficient);
  const toneText =
    tone === "pos"
      ? "text-pos"
      : tone === "warn"
        ? "text-warn"
        : tone === "neg"
          ? "text-neg"
          : "text-fg-muted";

  return (
    <div className="rounded-sm border border-info/25 bg-info/5 p-4">
      <div className="flex items-center gap-2 mb-2">
        <Badge tone="info" dot>
          <Target size={10} />
          #167 EDGE-GATE
        </Badge>
        <span className="text-2xs font-mono text-fg-subtle">{gate.source}</span>
      </div>

      <div className="flex items-baseline gap-2">
        <span className={cn("text-3xl font-mono font-semibold tabular-nums", toneText)}>
          {fmt(gate.value)}
        </span>
        <span className="text-lg font-mono text-fg-subtle">/ {gate.threshold}</span>
        {gate.ratio_pct != null && (
          <span className={cn("text-xs font-mono", toneText)}>
            {gate.ratio_pct.toFixed(0)} %
          </span>
        )}
      </div>

      <div className="mt-2">
        <ProgressBar
          value={gate.value}
          target={gate.threshold}
          label="resolved_real"
          sufficientSample={gate.value != null}
          size="md"
        />
      </div>

      <div className="mt-2 flex flex-col gap-1 text-2xs text-fg-muted">
        <span>
          <span className="font-mono text-fg">{gate.label}</span> · {gate.filter}
        </span>
        <span>{gate.measures}</span>
        <span className="text-info font-medium">→ {gate.watch_hint}</span>
      </div>
    </div>
  );
}

function OtherRow({
  label,
  value,
  measures,
  note,
}: {
  label: string;
  value: number | null;
  measures: string;
  note?: string | null;
}) {
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="w-14 shrink-0 text-right font-mono text-base tabular-nums text-fg-muted">
        {fmt(value)}
      </span>
      <ChevronRight size={12} className="shrink-0 text-fg-subtle" />
      <div className="min-w-0 flex-1">
        <span className="text-xs text-fg">{label}</span>
        <span className="text-2xs text-fg-subtle"> · {measures}</span>
      </div>
      {note && (
        <Badge tone="muted" className="shrink-0">
          {note}
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
          "Fünf verschiedene „resolved/n“ zählen verschiedene Pipelines. Nur eines steuert das #167-Gate."
        }
        right={
          <Badge
            tone="info"
            title={data?.trap_note}
          >
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
          <GateBlock gate={data.gate} />

          <div className="flex items-center gap-2 pt-1">
            <span className="text-2xs uppercase tracking-wide text-fg-subtle">
              andere Pipelines · nicht das Gate
            </span>
            <span className="h-px flex-1 bg-line" />
          </div>

          <div className="divide-y divide-line-subtle">
            {data.others.map((o) => (
              <OtherRow
                key={o.key}
                label={o.label}
                value={o.value}
                measures={o.measures}
                note={o.note}
              />
            ))}
          </div>

          <p className="text-2xs text-fg-subtle leading-relaxed pt-1">{data.trap_note}</p>
        </div>
      )}
    </Card>
  );
}
