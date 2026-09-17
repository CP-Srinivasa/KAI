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

// Prioritaets-Baender exakt wie app/alerts/formatters.py. Die Zahl allein sagt
// nichts: "7" liest sich klein, ist aber die Schwelle, ab der ein Alert
// ueberhaupt versendet wird (Alert-Gate filtert P<7). Band-Label dazu, Skala in
// den Tooltip — kein Ratespiel am Zahlenwert.
const PRIORITY_SCALE_TOOLTIP =
  "Prioritaet 1–10 (Quelle: app/alerts/audit.py). Baender: 1–3 niedrig · 4–6 mittel · " +
  "7–8 hoch · 9–10 kritisch. Ab 10 High-Conviction. Das Alert-Gate verwirft alles unter 7 — " +
  "was hier steht, hat das Gate passiert.";

function priorityBand(p: number): { label: string; tone: "muted" | "info" | "warn" | "neg" } {
  if (p >= 9) return { label: "kritisch", tone: "neg" };
  if (p >= 7) return { label: "hoch", tone: "warn" };
  if (p >= 4) return { label: "mittel", tone: "info" };
  return { label: "niedrig", tone: "muted" };
}

// Audit P0-4 (17.09.): TradingView-Webhooks tragen per Konstruktion keinen
// Analysewert. "—" sah fuer 77 % der Zeilen wie ein Datenfehler aus; jetzt
// steht dort, dass die Skala fuer diese Quelle nicht gilt.
const WEBHOOK_PRIORITY_TOOLTIP =
  "Webhook-Signal: TradingView liefert keinen Analysewert, deshalb gibt es hier " +
  "keine Prioritaet 1–10. Absichtlich nicht geschaetzt — ein Platzhalter wuerde " +
  "die Trefferquote je Prioritaet verfaelschen.";

function PriorityCell({
  p,
  basis,
}: {
  p: number | null | undefined;
  basis?: "analysis" | "webhook" | "unknown" | null;
}) {
  if (p == null) {
    if (basis === "webhook") {
      return (
        <Badge tone="muted" title={WEBHOOK_PRIORITY_TOOLTIP}>
          <span>Webhook</span>
        </Badge>
      );
    }
    return <span className="text-fg-subtle" title="Keine Prioritaet im Datensatz">—</span>;
  }
  const band = priorityBand(p);
  return (
    <Badge tone={band.tone} title={PRIORITY_SCALE_TOOLTIP}>
      <span className="font-mono font-semibold">{p}</span>
      <span className="text-fg-subtle/80">·</span>
      <span>{band.label}</span>
    </Badge>
  );
}

/** Quelle statt Hash. Der Vertrag liefert seit 2026-09-15 `source_name`
 *  (AlertAuditRecord). Fehlt er (alte Records), steht ehrlich "Quelle unbekannt"
 *  — der 12-Zeichen-Hash aus `doc_id` bleibt im title als Forensik-Anker und
 *  wird NICHT als Quelle ausgegeben. */
function sourceLabel(sourceName: string | null | undefined): string {
  const s = (sourceName ?? "").trim();
  return s.length > 0 ? s : "Quelle unbekannt";
}

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
                  title="Herkunft des Dokuments (News-Artikel, Telegram-Envelope, …). Die vollstaendige Dokument-ID steht im Tooltip der Zelle."
                >
                  Quelle
                </th>
                <th
                  className="text-left py-2 pr-3 font-medium"
                  title="Stimmung — steigend / fallend / neutral"
                >
                  Stimmung
                </th>
                <th
                  className="text-left py-2 pr-3 font-medium"
                  title={PRIORITY_SCALE_TOOLTIP}
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
                  <td
                    className="py-2 pr-3 text-xs text-fg-muted"
                    title={`Dokument-ID: ${a.doc_id}`}
                  >
                    {sourceLabel(a.source_name)}
                  </td>
                  <td className="py-2 pr-3">
                    <SentimentBadge s={a.sentiment} />
                  </td>
                  <td className="py-2 pr-3">
                    <PriorityCell p={a.priority} basis={a.priority_basis} />
                  </td>
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
