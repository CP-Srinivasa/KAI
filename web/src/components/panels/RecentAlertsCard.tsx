import { memo } from "react";
import { Inbox } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/EmptyState";
import type { DashboardQuality } from "@/lib/api";
import { formatAbsolute, formatRelative } from "@/lib/time";

type Props = { data: DashboardQuality | null };

function SentimentBadge({ s }: { s: string }) {
  if (!s) return <span className="text-fg-subtle">—</span>;
  const tone = s === "bullish" ? "pos" : s === "bearish" ? "neg" : "muted";
  return <Badge tone={tone}>{s}</Badge>;
}

function OutcomeBadge({ o }: { o: string }) {
  if (!o) return <Badge tone="muted">pending</Badge>;
  const tone = o === "hit" ? "pos" : o === "miss" ? "neg" : "muted";
  return <Badge tone={tone}>{o}</Badge>;
}

function RecentAlertsCardImpl({ data }: Props) {
  const rows = data?.recent_alerts ?? [];
  return (
    <Card padded>
      <CardHeader
        title="Letzte Directional Alerts"
        subtitle={`${rows.length} Einträge aus alert_audit.jsonl (jüngste zuerst)`}
        right={
          <Badge tone="muted" dot>
            live
          </Badge>
        }
      />
      {rows.length === 0 ? (
        <EmptyState
          icon={<Inbox size={18} />}
          title="Noch keine Alerts in diesem Fenster"
          hint="Directional Alerts erscheinen hier in Echtzeit, sobald die Pipeline sie dispatched. Quality-Report refreshed alle 30s."
          className="my-2"
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-2xs uppercase tracking-wide text-fg-subtle border-b border-line-subtle">
                <th className="text-left py-2 pr-3 font-medium">Doc</th>
                <th className="text-left py-2 pr-3 font-medium">Sentiment</th>
                <th className="text-center py-2 pr-3 font-medium">P</th>
                <th className="text-left py-2 pr-3 font-medium">Assets</th>
                <th className="text-left py-2 pr-3 font-medium">Dispatched</th>
                <th className="text-left py-2 pr-3 font-medium">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a, i) => (
                <tr
                  key={`${a.doc_id}-${i}`}
                  className="border-b border-line-subtle/60 last:border-0"
                >
                  <td className="py-2 pr-3 font-mono text-2xs text-fg-muted">{a.doc_id}</td>
                  <td className="py-2 pr-3">
                    <SentimentBadge s={a.sentiment} />
                  </td>
                  <td className="py-2 pr-3 text-center font-mono">{a.priority ?? "—"}</td>
                  <td className="py-2 pr-3 font-mono text-2xs text-fg-muted">
                    {a.assets.length ? a.assets.join(", ") : "—"}
                  </td>
                  <td
                    className="py-2 pr-3 text-xs text-fg-muted"
                    title={formatAbsolute(a.dispatched_at)}
                  >
                    {formatRelative(a.dispatched_at)}
                  </td>
                  <td className="py-2 pr-3">
                    <OutcomeBadge o={a.outcome} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export const RecentAlertsCard = memo(RecentAlertsCardImpl);
