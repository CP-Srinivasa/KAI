// @data-source: /dashboard/api/edge-timeseries
//
// KI-Insights Edge-Verlauf (#319 Frontend / Konzept §17): Precision/Brier/IC als
// Trend über die Zeitfenster aus dem resolved-Ledger. Zeichnet NUR Fenster mit
// belastbarer Stichprobe (Backend liefert null darunter) — keine irreführende
// Trendlinie auf dünnen Buckets. Ehrlich/kalibriert, kein Vorhersageversprechen.
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { Sparkline } from "@/components/kpi/Sparkline";
import { useApi } from "@/lib/useApi";
import { fetchEdgeTimeseries, type EdgeWindow } from "@/lib/api";
import { cn } from "@/lib/utils";

type MetricKey = "precision_pct" | "brier" | "ic_1h";

/** Pure: extrahiert eine Metrik-Serie (nur non-null-Fenster) + den letzten Wert. */
export function metricSeries(
  windows: EdgeWindow[],
  key: MetricKey,
): { points: { x: number; y: number }[]; latest: number | null } {
  const points: { x: number; y: number }[] = [];
  windows.forEach((w, i) => {
    const v = w[key];
    if (typeof v === "number") points.push({ x: i, y: v });
  });
  const latest = points.length > 0 ? points[points.length - 1].y : null;
  return { points, latest };
}

function TrendRow({
  label,
  windows,
  metricKey,
  tone,
  fmt,
}: {
  label: string;
  windows: EdgeWindow[];
  metricKey: MetricKey;
  tone: "info" | "warn" | "pos";
  fmt: (v: number) => string;
}) {
  const { points, latest } = metricSeries(windows, metricKey);
  const toneText = tone === "pos" ? "text-pos" : tone === "warn" ? "text-warn" : "text-info";
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="w-28 shrink-0 text-2xs uppercase tracking-wider text-fg-muted">{label}</span>
      <div className={cn("h-8 flex-1", toneText)}>
        {points.length >= 2 ? (
          <Sparkline data={points} stroke="currentColor" />
        ) : (
          <span className="text-2xs text-fg-subtle">zu wenig Daten (n &lt; min)</span>
        )}
      </div>
      <span className={cn("w-16 text-right font-mono text-sm font-semibold", latest != null ? toneText : "text-fg-subtle")}>
        {latest != null ? fmt(latest) : "—"}
      </span>
    </div>
  );
}

export function EdgeTrendCard() {
  const q = useApi(fetchEdgeTimeseries, 60_000);
  const data = q.state === "ready" ? q.data : null;
  const windows = data?.windows ?? [];
  const anyData = windows.some((w) => w.precision_pct != null || w.brier != null || w.ic_1h != null);
  const warming = !!data?.warming;

  return (
    <Card padded>
      <CardHeader
        title="Edge-Verlauf"
        subtitle={
          data
            ? `Precision / Brier / IC je ${data.bucket_days}d-Fenster · Gate n≥${data.min_resolved} · ehrlich, kein Vorhersageversprechen`
            : "Precision / Brier / IC über die Zeit"
        }
        right={
          q.state === "error" ? (
            <Badge tone="neg" dot>
              Endpoint-Fehler
            </Badge>
          ) : warming ? (
            <Badge tone="info" dot>
              berechne…
            </Badge>
          ) : !anyData ? (
            <Badge tone="muted" dot>
              keine belastbare Stichprobe
            </Badge>
          ) : null
        }
      />
      {q.state === "error" ? (
        <div className="py-2 text-xs text-neg">/dashboard/api/edge-timeseries unerreichbar.</div>
      ) : warming ? (
        <div className="py-2 text-xs text-fg-muted">
          Edge-Verlauf wird im Hintergrund berechnet — erscheint beim nächsten Refresh.
        </div>
      ) : !anyData ? (
        <div className="py-2 text-xs text-fg-muted">
          Noch kein Zeitfenster über der Mindest-Stichprobe — Trend erscheint, sobald genug aufgelöste Signale je Fenster vorliegen.
        </div>
      ) : (
        <div className="divide-y divide-line-subtle">
          <TrendRow label="Precision" windows={windows} metricKey="precision_pct" tone="pos" fmt={(v) => `${v.toFixed(1)}%`} />
          <TrendRow label="Brier" windows={windows} metricKey="brier" tone="warn" fmt={(v) => v.toFixed(3)} />
          <TrendRow label="IC (1h)" windows={windows} metricKey="ic_1h" tone="info" fmt={(v) => v.toFixed(2)} />
        </div>
      )}
    </Card>
  );
}
