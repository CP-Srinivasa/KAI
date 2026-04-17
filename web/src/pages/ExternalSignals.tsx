import { useState } from "react";
import { Send, Loader2, RefreshCw, Inbox, CheckCircle2, AlertTriangle, XCircle, Clock } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { PageHeader } from "@/layout/PageHeader";
import { Badge, Button, Card, CardHeader } from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import { cn } from "@/lib/utils";
import {
  ApiError,
  fetchRecentEnvelopes,
  postSignalPaste,
  type EnvelopeRecentResponse,
  type EnvelopeRecord,
  type SignalPasteResponse,
  type SignalPasteStatus,
} from "@/lib/api";

const SAMPLE = `[SIGNAL]
Signal ID: SIG-YYYYMMDD-BTCUSDT-001
Source: Dashboard
Exchange Scope: binance_futures
Symbol: BTC/USDT
Side: BUY
Direction: LONG
Entry Rule: BELOW 65000
Targets: 70000
Stop Loss: 62000
Leverage: 10x
Status: NEW
Timestamp: 2026-04-15T10:00:00Z
`;

type Tone = "pos" | "warn" | "neg" | "info" | "neutral";

function statusTone(status: SignalPasteStatus): "pos" | "warn" | "neg" {
  if (status === "accepted") return "pos";
  if (status === "duplicate") return "warn";
  return "neg";
}

function envelopeTone(rec: EnvelopeRecord): Tone {
  const { status, stage } = rec;
  if (status === "ok" || stage === "accepted") return "pos";
  if (status === "duplicate") return "warn";
  if (status === "rejected" || status === "blocked") return "neg";
  if (stage === "voice_confirm_gate" && status === "draft_pending") return "info";
  return "neutral";
}

const TONE_ACCENT: Record<Tone, string> = {
  pos: "border-l-pos",
  warn: "border-l-warn",
  neg: "border-l-neg",
  info: "border-l-info",
  neutral: "border-l-fg-subtle",
};

const TONE_ICON: Record<Tone, JSX.Element> = {
  pos: <CheckCircle2 className="h-4 w-4 text-pos" />,
  warn: <AlertTriangle className="h-4 w-4 text-warn" />,
  neg: <XCircle className="h-4 w-4 text-neg" />,
  info: <Clock className="h-4 w-4 text-info" />,
  neutral: <Inbox className="h-4 w-4 text-fg-subtle" />,
};

function formatTs(value: string | null): string {
  if (!value) return "—";
  const cleaned = value.replace("T", " ").replace(/\.\d+/, "").replace("+00:00", "Z");
  return cleaned;
}

function relativeTs(value: string | null): string {
  if (!value) return "";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return "";
  const deltaSec = Math.max(0, (Date.now() - parsed) / 1000);
  if (deltaSec < 60) return `vor ${Math.floor(deltaSec)}s`;
  if (deltaSec < 3600) return `vor ${Math.floor(deltaSec / 60)}m`;
  if (deltaSec < 86400) return `vor ${Math.floor(deltaSec / 3600)}h`;
  return `vor ${Math.floor(deltaSec / 86400)}d`;
}

type SignalPasteFormProps = {
  onPasted: () => void;
};

function SignalPasteForm({ onPasted }: SignalPasteFormProps) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SignalPasteResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await postSignalPaste(text);
      setResult(res);
      onPasted();
    } catch (e) {
      if (e instanceof ApiError) setError(`${e.kind} (${e.status}): ${e.message}`);
      else setError((e as Error).message || "Fehler");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Signal-Paste"
        subtitle="Strukturierter Block ([SIGNAL] / [NEWS] / [EXCHANGE_RESPONSE]) läuft durch die Envelope-Pipeline: parse → schema → idempotency → audit."
        right={
          <Badge tone="info" dot>
            POST /signals/paste
          </Badge>
        }
      />
      <div className="grid gap-4 lg:grid-cols-[1fr_minmax(280px,360px)]">
        {/* Paste-Feld */}
        <div className="space-y-3">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={SAMPLE}
            spellCheck={false}
            className="w-full min-h-[260px] rounded-sm border border-line bg-bg-0 p-3 font-mono text-xs text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent/40"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="md"
              onClick={submit}
              disabled={busy || !text.trim()}
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Send className="h-3.5 w-3.5" />
              )}
              Absenden
            </Button>
            <Button
              variant="ghost"
              size="md"
              onClick={() => {
                setText("");
                setResult(null);
                setError(null);
              }}
              disabled={busy}
            >
              Zurücksetzen
            </Button>
            <Button
              variant="ghost"
              size="md"
              onClick={() => setText(SAMPLE)}
              disabled={busy}
            >
              Beispiel einfügen
            </Button>
          </div>
        </div>

        {/* Response-Pane */}
        <div className="space-y-3">
          <div className="text-2xs font-semibold uppercase tracking-[0.1em] text-fg-subtle">
            Letzte Antwort
          </div>
          {!result && !error && (
            <div className="rounded-sm border border-dashed border-line-subtle bg-bg-0 p-4 text-xs text-fg-subtle">
              Noch kein Paste abgesendet.
            </div>
          )}
          {error && (
            <div className="rounded-sm border border-neg/30 bg-neg/5 p-3 text-xs text-neg">
              {error}
            </div>
          )}
          {result && (
            <div
              className={cn(
                "rounded-sm border bg-bg-0 p-3 text-xs space-y-2 border-l-4",
                TONE_ACCENT[statusTone(result.status)],
                "border-line-subtle",
              )}
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={statusTone(result.status)} dot>
                  {result.status}
                </Badge>
                <Badge tone="muted">{result.stage}</Badge>
                {result.message_type && (
                  <Badge tone="info">{result.message_type}</Badge>
                )}
              </div>
              {result.envelope_id && (
                <div className="space-y-0.5">
                  <div className="text-2xs uppercase tracking-wider text-fg-subtle">
                    Envelope-ID
                  </div>
                  <div className="font-mono text-xs text-fg break-all">
                    {result.envelope_id}
                  </div>
                </div>
              )}
              {result.idempotency_key && (
                <div className="space-y-0.5">
                  <div className="text-2xs uppercase tracking-wider text-fg-subtle">
                    Idempotency-Key
                  </div>
                  <div className="font-mono text-2xs text-fg-muted break-all">
                    {result.idempotency_key}
                  </div>
                </div>
              )}
              {result.errors.length > 0 && (
                <div className="space-y-1">
                  <div className="text-2xs uppercase tracking-wider text-neg">
                    Fehler
                  </div>
                  <ul className="list-disc pl-5 text-neg space-y-0.5">
                    {result.errors.map((err, i) => (
                      <li key={i}>{err}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function EnvelopeCard({ rec }: { rec: EnvelopeRecord }) {
  const tone = envelopeTone(rec);
  return (
    <div
      className={cn(
        "rounded-sm border border-line-subtle bg-bg-0 p-4 border-l-4 space-y-3 transition-colors hover:bg-bg-2/40",
        TONE_ACCENT[tone],
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="shrink-0">{TONE_ICON[tone]}</span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={tone === "neutral" ? "muted" : tone} dot>
                {rec.status ?? "—"}
              </Badge>
              <Badge tone="muted">{rec.stage ?? "—"}</Badge>
              {rec.message_type && (
                <Badge tone="info">{rec.message_type}</Badge>
              )}
              {rec.source && (
                <span className="text-2xs uppercase tracking-wider text-fg-subtle">
                  src: {rec.source}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-2xs font-mono text-fg-muted whitespace-nowrap">
            {formatTs(rec.timestamp_utc)}
          </div>
          <div className="text-2xs text-fg-subtle">
            {relativeTs(rec.timestamp_utc)}
          </div>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {rec.envelope_id && (
          <div>
            <div className="text-2xs uppercase tracking-wider text-fg-subtle mb-0.5">
              Envelope-ID
            </div>
            <div className="font-mono text-xs text-fg break-all">
              {rec.envelope_id}
            </div>
          </div>
        )}
        {rec.idempotency_key && (
          <div>
            <div className="text-2xs uppercase tracking-wider text-fg-subtle mb-0.5">
              Idempotency-Key
            </div>
            <div className="font-mono text-2xs text-fg-muted break-all">
              {rec.idempotency_key}
            </div>
          </div>
        )}
      </div>

      {rec.errors.length > 0 && (
        <div className="rounded-sm border border-neg/20 bg-neg/5 p-2.5">
          <div className="text-2xs uppercase tracking-wider text-neg mb-1">
            Fehler
          </div>
          <ul className="list-disc pl-5 text-xs text-neg space-y-0.5">
            {rec.errors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

type AuditPanelProps = {
  state: ReturnType<typeof useApi<EnvelopeRecentResponse>>;
};

function AuditLogPanel({ state }: AuditPanelProps) {
  const records = state.state === "ready" ? state.data.records : [];
  const count = state.state === "ready" ? state.data.count : 0;

  return (
    <Card>
      <CardHeader
        title="Audit-Log"
        subtitle="Letzte Envelope-Einträge aus artifacts/telegram_message_envelope.jsonl (Dashboard + Telegram geteilt, newest first)."
        right={
          <div className="flex items-center gap-2">
            {state.state === "ready" && (
              <Badge tone="muted">{count} Einträge</Badge>
            )}
            <Button variant="outline" size="sm" onClick={state.reload}>
              <RefreshCw className="h-3.5 w-3.5" />
              Reload
            </Button>
          </div>
        }
      />

      {state.state === "loading" && (
        <div className="flex items-center gap-2 text-xs text-fg-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Lade Audit-Log …
        </div>
      )}
      {state.state === "error" && (
        <div className="rounded-sm border border-neg/30 bg-neg/5 p-3 text-xs text-neg break-words">
          Fehler: {state.error.kind} ({state.error.status}) — {state.error.message}
        </div>
      )}
      {state.state === "ready" && records.length === 0 && (
        <div className="rounded-sm border border-dashed border-line-subtle bg-bg-0 p-6 text-center text-xs text-fg-subtle">
          <Inbox className="h-6 w-6 mx-auto mb-2 opacity-50" />
          Noch keine Einträge. Paste oben ein Signal oder warte auf Telegram-Traffic.
        </div>
      )}
      {state.state === "ready" && records.length > 0 && (
        <div className="space-y-2.5">
          {records.map((rec, i) => (
            <EnvelopeCard
              key={`${rec.envelope_id ?? "na"}-${rec.timestamp_utc ?? i}-${i}`}
              rec={rec}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

export function ExternalSignalsPage() {
  const { t } = useT();
  const auditState = useApi<EnvelopeRecentResponse>(
    (signal) => fetchRecentEnvelopes(50, signal),
    30_000,
  );

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.external.title")}
        sub="Manuelle & externe Signale — Dashboard/Telegram-Parität über Envelope-Pipeline"
      />

      <SignalPasteForm onPasted={auditState.reload} />
      <AuditLogPanel state={auditState} />
    </div>
  );
}
