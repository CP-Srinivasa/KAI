// @data-source: props (parent-provided)
import { memo } from "react";
import { Inbox } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import { EmptyState } from "@/components/ui/EmptyState";
import type { DashboardQuality } from "@/lib/api";
import { formatAbsolute, formatRelative } from "@/lib/time";

type Props = {
  data: DashboardQuality | null;
  state: "loading" | "ready" | "error";
  generatedAt: string | null;
};

// DALI v2 S3 M1c: Sentiment + Outcome auf deutsche Klartext-Wörter,
// raw-key bleibt als title fuer Forensik/Debug.
const SENTIMENT_LABEL: Record<string, string> = {
  bullish: "steigend",
  bearish: "fallend",
  neutral: "neutral",
};

const OUTCOME_LABEL: Record<string, string> = {
  hit: "Treffer",
  miss: "Fehler",
  pending: "offen",
};

function SentimentBadge({ s }: { s: string }) {
  if (!s) return <span className="text-fg-subtle">—</span>;
  const tone = s === "bullish" ? "pos" : s === "bearish" ? "neg" : "muted";
  return (
    <Badge tone={tone} title={`Sentiment: ${s}`}>
      {SENTIMENT_LABEL[s] ?? s}
    </Badge>
  );
}

function OutcomeBadge({ o }: { o: string }) {
  if (!o) {
    return (
      <Badge tone="muted" title="outcome: pending">
        {OUTCOME_LABEL.pending}
      </Badge>
    );
  }
  const tone = o === "hit" ? "pos" : o === "miss" ? "neg" : "muted";
  return (
    <Badge tone={tone} title={`outcome: ${o}`}>
      {OUTCOME_LABEL[o] ?? o}
    </Badge>
  );
}

function RecentAlertsCardImpl({ data, state, generatedAt }: Props) {
  const rows = data?.recent_alerts ?? [];
  return (
    <Card padded>
      <CardHeader
        title="Letzte Directional Alerts"
        subtitle={`${rows.length} jüngste Alerts — was wurde gemeldet, wie ist das Ergebnis?`}
        right={<LiveDot state={state} generatedAt={generatedAt} />}
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
              {/* DALI v2 S3 M1c: Klartext-Spalten mit title-Tooltipps fuer
                  Bedeutung (Master-Spec G1 + G2). Raw-Begriffe in title.  */}
              <tr className="text-2xs uppercase tracking-wide text-fg-subtle border-b border-line-subtle">
                <th
                  className="text-left py-2 pr-3 font-medium"
                  title="Dokument-ID aus der Quelle (News-Artikel, Telegram-Envelope, …)"
                >
                  Dokument
                </th>
                <th
                  className="text-left py-2 pr-3 font-medium"
                  title="Stimmung — steigend / fallend / neutral"
                >
                  Stimmung
                </th>
                <th
                  className="text-center py-2 pr-3 font-medium"
                  title="Priorität 1–10. P≥7 = Premium-Signal."
                >
                  Priorität
                </th>
                <th
                  className="text-left py-2 pr-3 font-medium"
                  title="Vom Signal betroffene Assets"
                >
                  Assets
                </th>
                <th
                  className="text-left py-2 pr-3 font-medium"
                  title="Wann wurde der Alert versendet"
                >
                  Versendet
                </th>
                <th
                  className="text-left py-2 pr-3 font-medium"
                  title="Ergebnis nach Forward-Window — Treffer / Fehler / offen"
                >
                  Ergebnis
                </th>
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
