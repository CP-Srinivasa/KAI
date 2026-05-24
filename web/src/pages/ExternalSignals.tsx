import { useEffect, useMemo, useState } from "react";
import {
  Send,
  Loader2,
  RefreshCw,
  Inbox,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Clock,
  HelpCircle,
  Upload,
  Sparkles,
  RotateCcw,
  FileText,
  MessageSquare,
  Target,
} from "lucide-react";
import { useRouter } from "@/state/Router";
import { useT } from "@/i18n/I18nProvider";
import { PageHeader } from "@/layout/PageHeader";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  InfoHint,
  Kpi,
  SectionLabel,
} from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import { cn } from "@/lib/utils";
import {
  ENVELOPE_SOURCE_LABEL,
  ENVELOPE_STAGE_LABEL,
  PASTE_STATUS_LABEL,
  PASTE_STATUS_SHORT,
  PASTE_STEPPER_STEPS,
  TERM_EXPLAIN,
} from "@/lib/labels";
import {
  ApiError,
  fetchRecentEnvelopes,
  postSignalPaste,
  type EnvelopeRecentResponse,
  type EnvelopeRecord,
  type SignalCompletionFields,
  type SignalPasteResponse,
  type SignalPasteStatus,
  type SignalSummary,
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

const SAMPLE_FREEFORM = `🟢 #BTC/USDT LONG/BUY
Entry Zone: 70565 – 70590
Targets:
🎯 70965
🎯 71200
🎯 71400
Stop Loss: 70400
Leverage: 10x
`;

type Tone = "pos" | "warn" | "neg" | "info" | "neutral";

function statusTone(status: SignalPasteStatus): "pos" | "warn" | "neg" | "info" {
  if (status === "accepted") return "pos";
  if (status === "duplicate") return "warn";
  if (status === "needs_completion") return "info";
  return "neg";
}

const KNOWN_EXCHANGES = [
  "binance_futures",
  "bybit",
  "okx",
  "bitget",
  "kucoin",
  "mexc",
  "deribit",
  "bingx",
  "blofin",
  "huobi",
] as const;

function envelopeTone(rec: EnvelopeRecord): Tone {
  const { status, stage } = rec;
  if (status === "duplicate") return "warn";
  if (status === "ok" || stage === "accepted") return "pos";
  if (status === "rejected" || status === "blocked") return "neg";
  if (stage === "voice_confirm_gate" && status === "draft_pending") return "info";
  return "neutral";
}

function statusHeadline(rec: EnvelopeRecord): string {
  if (rec.status === "duplicate") return "Duplikat verhindert";
  if (rec.status === "ok" || rec.stage === "accepted") return "Sauber raus — OK";
  if (rec.status === "rejected" || rec.status === "blocked") return "Abgelehnt";
  if (rec.stage === "voice_confirm_gate") return "Wartet auf Bestätigung";
  return rec.status ?? "—";
}

// DALI-T7: humanisiere Source-Tag (telegram → "Telegram" etc.).
function humanizeSource(src: string | null | undefined): string {
  if (!src) return "Unbekannt";
  const key = src.toLowerCase();
  return ENVELOPE_SOURCE_LABEL[key] ?? src;
}

// DALI-T7: humanisiere Stage (parse → "Nachricht analysiert" etc.).
function humanizeStage(stage: string | null | undefined): string {
  if (!stage) return "—";
  return ENVELOPE_STAGE_LABEL[stage] ?? stage;
}

function formatNumber(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString("de-DE", {
    maximumFractionDigits: 8,
    useGrouping: false,
  });
}

function formatEntryRule(signal: SignalSummary): string {
  if (signal.entry_value == null) return "—";
  const price = formatNumber(signal.entry_value);
  switch ((signal.entry_type ?? "").toLowerCase()) {
    case "below":
      return `BELOW ${price}`;
    case "above":
      return `ABOVE ${price}`;
    case "at":
      return `AT ${price}`;
    default:
      return price;
  }
}

function formatLeverage(value: number | null): string {
  if (value == null) return "—";
  if (value <= 1) return "1x (spot / kein Leverage)";
  return `${value}x`;
}

function formatDirection(signal: SignalSummary): string {
  const dir = (signal.direction ?? "").toUpperCase();
  const side = (signal.side ?? "").toUpperCase();
  if (dir && side && dir !== side) return `${dir} / ${side}`;
  return dir || side || "—";
}

function signalHeadline(signal: SignalSummary): string {
  const parts: string[] = [];
  const symbol = signal.symbol ?? "—";
  const dir = formatDirection(signal);
  parts.push(dir && dir !== "—" ? `${symbol} ${dir}` : symbol);
  if (signal.entry_value != null) {
    parts.push(`@ ${formatEntryRule(signal)}`);
  }
  if (signal.targets.length > 0) {
    const tgt = signal.targets.slice(0, 3).map(formatNumber).join(" · ");
    const tail = signal.targets.length > 3 ? ` +${signal.targets.length - 3}` : "";
    parts.push(`🎯 ${tgt}${tail}`);
  }
  if (signal.stop_loss != null) {
    parts.push(`SL ${formatNumber(signal.stop_loss)}`);
  }
  if (signal.leverage != null && signal.leverage > 1) {
    parts.push(`${signal.leverage}x`);
  }
  const exCount = signal.exchange_scope.length;
  if (exCount > 0) {
    parts.push(exCount === 1 ? signal.exchange_scope[0] : `${exCount} Exchanges`);
  }
  return parts.join(" · ");
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


// DALI-T7: 6-Schritt-Stepper im Synthwave-Stil, abgeleitet vom T6-Cycle-
// Pipeline-Pattern aus Signals.tsx.
type PasteStepperProps = {
  activeIdx: number;
  failedIdx?: number;
  className?: string;
};

function PasteStepper({ activeIdx, failedIdx = -1, className }: PasteStepperProps) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 sm:flex-row sm:items-start sm:gap-0",
        className,
      )}
      role="list"
      aria-label="Signal-Verarbeitungs-Pipeline"
    >
      {PASTE_STEPPER_STEPS.map((step, i) => {
        const isLast = i === PASTE_STEPPER_STEPS.length - 1;
        const isFailed = failedIdx === i;
        const isActive = activeIdx === i;
        const isReached = activeIdx > i || activeIdx === PASTE_STEPPER_STEPS.length;
        const dotTone = isFailed
          ? "bg-neg glow-neg animate-pulse"
          : isActive
            ? "bg-info glow-info animate-pulse"
            : isReached
              ? "bg-info glow-info"
              : "bg-fg-subtle/25";
        const lineTone = isReached || isActive ? "bg-info/45" : "bg-fg-subtle/15";
        const textTone = isFailed
          ? "text-neg"
          : isActive
            ? "text-info font-medium"
            : isReached
              ? "text-fg-muted"
              : "text-fg-subtle/80";
        return (
          <div
            key={step.key}
            role="listitem"
            className="flex sm:flex-col items-start sm:items-center sm:flex-1 gap-2 sm:gap-1 min-w-0"
            title={(i + 1) + ". " + step.short + " — " + step.explain}
          >
            <div className="flex items-center sm:flex-col sm:items-center shrink-0">
              <span
                className={cn(
                  "inline-block h-2.5 w-2.5 rounded-full shrink-0 transition-transform hover:scale-150",
                  dotTone,
                )}
                aria-hidden
              />
              {!isLast && (
                <span
                  className={cn(
                    "hidden sm:inline-block h-px shrink-0 mt-0 sm:w-full sm:max-w-[60px] sm:mx-1",
                    lineTone,
                  )}
                  aria-hidden
                />
              )}
            </div>
            <div className="flex flex-col min-w-0 sm:items-center sm:text-center">
              <span
                className={cn(
                  "text-2xs uppercase tracking-wider leading-tight whitespace-nowrap",
                  textTone,
                )}
              >
                {i + 1}. {step.short}
              </span>
              <span className="sm:hidden text-2xs text-fg-subtle/70 leading-snug mt-0.5 break-words">
                {step.explain}
              </span>
            </div>
            {!isLast && (
              <span
                className={cn("sm:hidden ml-1 inline-block h-3 w-px shrink-0", lineTone)}
                aria-hidden
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// DALI-T7: Triage-Strip — Counts aus records, gefiltert auf heute (UTC).
function todayCounts(records: EnvelopeRecord[]) {
  const today = new Date();
  const start = Date.UTC(
    today.getUTCFullYear(),
    today.getUTCMonth(),
    today.getUTCDate(),
  );
  let accepted = 0;
  let duplicate = 0;
  let needs = 0;
  let rejected = 0;
  for (const rec of records) {
    if (!rec.timestamp_utc) continue;
    const ts = Date.parse(rec.timestamp_utc);
    if (Number.isNaN(ts) || ts < start) continue;
    if (rec.status === "ok" || rec.stage === "accepted") accepted++;
    else if (rec.status === "duplicate") duplicate++;
    else if (rec.status === "rejected" || rec.status === "blocked") rejected++;
    else if (rec.stage === "voice_confirm_gate" || rec.status === "draft_pending")
      needs++;
  }
  return { accepted, duplicate, needs, rejected };
}

type SignalPasteFormProps = {
  onPasted: () => void;
};

function SignalPasteForm({ onPasted }: SignalPasteFormProps) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SignalPasteResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exchangeChoice, setExchangeChoice] = useState<string>("");
  const [stopLossInput, setStopLossInput] = useState<string>("");
  const [targetsInput, setTargetsInput] = useState<string>("");
  const [leverageInput, setLeverageInput] = useState<string>("");

  // DALI-T7: -1 = idle, 0..5 = laufend, 6 = fertig.
  const [stepIdx, setStepIdx] = useState<number>(-1);
  const [failIdx, setFailIdx] = useState<number>(-1);

  // Prefill verify-and-submit form from parsed preview on needs_completion.
  useEffect(() => {
    if (!result || result.status !== "needs_completion") return;
    const preview = result.parsed_preview ?? {};
    const ex = preview.exchange_scope;
    if (Array.isArray(ex) && ex.length > 0 && typeof ex[0] === "string") {
      setExchangeChoice(ex[0] as string);
    } else {
      setExchangeChoice("");
    }
    const sl = preview.stop_loss;
    setStopLossInput(typeof sl === "number" ? String(sl) : "");
    const tg = preview.targets;
    setTargetsInput(
      Array.isArray(tg) && tg.length > 0
        ? tg.filter((v) => typeof v === "number").join(", ")
        : "",
    );
    const lev = preview.leverage;
    setLeverageInput(typeof lev === "number" && lev > 1 ? String(lev) : "");
  }, [result]);

  // Final stepper state nach Response.
  useEffect(() => {
    if (!result) return;
    if (result.status === "accepted" || result.status === "duplicate") {
      setStepIdx(PASTE_STEPPER_STEPS.length);
      setFailIdx(-1);
    } else if (result.status === "needs_completion") {
      setStepIdx(2);
      setFailIdx(-1);
    } else if (result.status === "rejected") {
      setStepIdx(2);
      setFailIdx(2);
    }
  }, [result]);

  async function submit(extra?: SignalCompletionFields) {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    setStepIdx(0);
    setFailIdx(-1);
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (let i = 1; i <= 4; i++) {
      timers.push(
        setTimeout(
          () => setStepIdx((cur) => (cur < i && cur >= 0 ? i : cur)),
          i * 180,
        ),
      );
    }
    try {
      const res = await postSignalPaste(
        text,
        extra ? { completion_fields: extra } : undefined,
      );
      setResult(res);
      if (res.status === "accepted" || res.status === "duplicate") {
        setExchangeChoice("");
        setStopLossInput("");
        setTargetsInput("");
        setLeverageInput("");
      }
      onPasted();
    } catch (e) {
      if (e instanceof ApiError) setError(e.kind + " (" + e.status + "): " + e.message);
      else setError((e as Error).message || "Fehler");
      setStepIdx(2);
      setFailIdx(2);
    } finally {
      timers.forEach(clearTimeout);
      setBusy(false);
    }
  }

  function submitWithCompletion() {
    if (!result || result.status !== "needs_completion") return;
    const completion: SignalCompletionFields = {};
    if (exchangeChoice.trim()) {
      completion.exchange_scope = [exchangeChoice.trim()];
    }
    if (stopLossInput.trim()) {
      const v = Number(stopLossInput.replace(",", "."));
      if (Number.isFinite(v) && v > 0) completion.stop_loss = v;
    }
    if (targetsInput.trim()) {
      const parts = targetsInput
        .split(/[\s,;]+/)
        .map((p) => Number(p.replace(",", ".")))
        .filter((v) => Number.isFinite(v) && v > 0);
      if (parts.length > 0) completion.targets = parts;
    }
    if (leverageInput.trim()) {
      const v = Number(leverageInput);
      if (Number.isInteger(v) && v >= 1) completion.leverage = v;
    }
    submit(completion);
  }

  function resetAll() {
    setText("");
    setResult(null);
    setError(null);
    setStepIdx(-1);
    setFailIdx(-1);
    setExchangeChoice("");
    setStopLossInput("");
    setTargetsInput("");
    setLeverageInput("");
  }

  const statusLong = result ? PASTE_STATUS_LABEL[result.status] ?? result.status : "";
  const statusShort = result ? PASTE_STATUS_SHORT[result.status] ?? result.status : "";

  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            Externe Trading-Signale einspeisen
            <InfoHint
              label="Was unterstützt KAI?"
              hint="Strukturiertes [SIGNAL]/[NEWS]/[EXCHANGE_RESPONSE] und freie Telegram-Formate. Erkannt werden: Paar (BTC/USDT), Richtung (LONG/SHORT bzw. BUY/SELL), Entry-Zone, Stop Loss, Targets, Leverage, Exchange-Scope. KAI parst, validiert, dedupliziert und schreibt jede Verarbeitung ins Audit-Log."
            />
          </span>
        }
        subtitle={
          <>
            LONG/SHORT, BUY/SELL, Trading-Paare, Entry-Zonen, SL/TP &mdash;
            strukturiert ODER freies Telegram-Format. KAI analysiert, validiert
            und dokumentiert jede Eingabe.
          </>
        }
        right={
          <Badge tone="muted" className="font-mono">
            POST /signals/paste
          </Badge>
        }
      />

      {/* DALI-T7: 6-Schritt-Stepper als visuelle Prozess-Leiste. */}
      <div className="mb-5 rounded-sm border border-line-subtle bg-bg-0 p-3">
        <PasteStepper activeIdx={stepIdx} failedIdx={failIdx} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_minmax(280px,360px)]">
        {/* Paste-Feld */}
        <div className="space-y-3">
          {/*
            DALI-T7 Fix: integriertes Neon-Top-Border am Textarea-Wrapper
            (statt schwebender PageHeader-Synthwave-Divider darueber). Der
            obere ::before-Pseudo-Gradient sitzt FEST auf der Top-Kante der
            Paste-Box; box-shadow gibt subtilen Glow nach oben.
          */}
          <div
            className={cn(
              "relative rounded-sm",
              "before:content-[''] before:absolute before:inset-x-0 before:top-0 before:h-px",
              "before:bg-gradient-to-r before:from-info/0 before:via-info/70 before:to-ai/0",
              "before:rounded-sm before:pointer-events-none",
              "before:shadow-[0_-2px_8px_-1px_rgba(56,189,248,0.35)]",
              "dark:before:shadow-[0_-2px_10px_-1px_rgba(168,85,247,0.45)]",
            )}
          >
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={SAMPLE}
              spellCheck={false}
              aria-label="Signal-Text eingeben"
              className={cn(
                "w-full min-h-[260px] rounded-sm border border-line bg-bg-0 p-3 pt-3.5",
                "font-mono text-xs text-fg placeholder:text-fg-subtle",
                "focus:outline-none focus:ring-2 focus:ring-accent/40",
                "border-t-info/40",
              )}
            />
          </div>

          {/* DALI-T7 Buttons: Haupt-CTA gross+glow, Reset, Beispiel-Trio. */}
          <div className="flex flex-col sm:flex-row sm:flex-wrap items-stretch sm:items-center gap-2">
            <Button
              variant="primary"
              size="md"
              onClick={() => submit()}
              disabled={busy || !text.trim()}
              className={cn(
                "h-10 px-5 text-sm font-semibold tracking-wide uppercase",
                "shadow-[0_0_18px_-2px_rgba(99,102,241,0.55)] glow-info",
                "w-full sm:w-auto",
              )}
              aria-label="Signal an KAI senden und analysieren"
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="h-4 w-4" />
              )}
              Signal analysieren
            </Button>

            <Button
              variant="outline"
              size="md"
              onClick={resetAll}
              disabled={busy}
              aria-label="Eingabe und Antwort zurücksetzen"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Eingabe zurücksetzen
            </Button>

            {/* Hilfe-Gruppe rechts — ghost-Buttons. */}
            <div className="flex flex-wrap items-center gap-1 sm:ml-auto">
              <SectionLabel className="mr-1 hidden sm:inline">
                Beispiel laden:
              </SectionLabel>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setText(SAMPLE)}
                disabled={busy}
                title="Strukturiertes [SIGNAL]-Beispiel in die Paste-Box laden."
              >
                <FileText className="h-3.5 w-3.5" />
                Strukturiert
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setText(SAMPLE_FREEFORM)}
                disabled={busy}
                title="Freies Telegram-Format (Emoji + Kurzschrift) in die Paste-Box laden."
              >
                <MessageSquare className="h-3.5 w-3.5" />
                Telegram-Stil
              </Button>
            </div>
          </div>
        </div>

        {/* DALI-T7 Response-Pane */}
        <div className="space-y-3">
          <SectionLabel>
            <span className="inline-flex items-center gap-1.5">
              Verarbeitungs-Antwort von KAI
              <InfoHint
                label="Antwort-Inhalt"
                hint="Status, Verarbeitungs-Stage, ggf. Envelope-ID und Idempotency-Key. Bei needs_completion erscheint hier das Ergänzungs-Formular."
                side="left"
              />
            </span>
          </SectionLabel>

          {!result && !error && (
            <div className="rounded-sm border border-dashed border-line-subtle bg-bg-0 p-4 text-xs space-y-1.5">
              <div className="text-fg font-medium">
                Noch kein externes Signal verarbeitet
              </div>
              <div className="text-fg-subtle leading-relaxed">
                Sobald du ein Signal einfügst und auf{" "}
                <span className="text-info font-medium">Signal analysieren</span>{" "}
                klickst, erscheint hier die Analyse- und Verarbeitungsantwort
                von KAI.
              </div>
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
                "rounded-sm border border-line bg-bg-0 p-3 text-xs space-y-2.5 border-l-4",
                TONE_ACCENT[statusTone(result.status)],
              )}
            >
              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={statusTone(result.status)} dot>
                    {statusShort}
                  </Badge>
                  <Badge tone="muted">
                    {humanizeStage(result.stage)}
                  </Badge>
                  {result.message_type && (
                    <Badge tone="info">{result.message_type}</Badge>
                  )}
                </div>
                <div className="text-xs text-fg-muted leading-relaxed">
                  {statusLong}
                </div>
              </div>

              {result.status === "needs_completion" && (
                <div className="space-y-3 rounded-sm border border-info/30 bg-info/5 p-3">
                  <div className="flex items-start gap-2 text-info">
                    <HelpCircle className="h-4 w-4 shrink-0 mt-0.5" />
                    <div className="text-xs">
                      <div className="font-semibold inline-flex items-center gap-1.5">
                        KAI braucht zusätzliche Angaben
                        <InfoHint
                          label="needs_completion"
                          hint={TERM_EXPLAIN.needs_completion}
                          side="left"
                        />
                      </div>
                      <div className="text-fg-muted leading-relaxed">
                        Das Signal ist heuristisch lesbar, aber Pflichtfelder
                        fehlen. Bitte jetzt ergänzen oder korrigieren &mdash;
                        ohne vollständige Angaben setzt KAI keine stillen
                        Defaults.
                      </div>
                    </div>
                  </div>
                  {result.parsed_preview && (
                    <div className="rounded-sm border border-line bg-bg-0 p-2 space-y-0.5">
                      <div className="text-2xs uppercase tracking-wider text-fg-subtle mb-1">
                        Was KAI bisher erkannt hat
                      </div>
                      <div className="font-mono text-2xs text-fg-muted space-y-0.5">
                        {Object.entries(result.parsed_preview).map(([k, v]) => (
                          <div key={k} className="flex gap-2">
                            <span className="text-fg-subtle">{k}:</span>
                            <span className="break-all">
                              {Array.isArray(v)
                                ? v.length === 0
                                  ? "—"
                                  : v.join(", ")
                                : v === null
                                  ? "—"
                                  : String(v)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="space-y-2">
                    <div className="text-2xs uppercase tracking-wider text-fg-subtle">
                      Fehlende Felder ergänzen
                    </div>
                    <label className="block space-y-1">
                      <span className="text-2xs uppercase tracking-wider text-fg-subtle flex items-center gap-1">
                        Exchange
                        <InfoHint
                          label="exchange_scope"
                          hint={TERM_EXPLAIN.exchange_scope}
                        />
                        {result.missing_fields.includes("exchange_scope") && (
                          <Badge tone="warn">erforderlich</Badge>
                        )}
                      </span>
                      <select
                        value={exchangeChoice}
                        onChange={(e) => setExchangeChoice(e.target.value)}
                        className="w-full rounded-sm border border-line bg-bg-0 px-2 py-1 text-xs text-fg focus:outline-none focus:ring-2 focus:ring-accent/40"
                      >
                        <option value="">— bitte wählen —</option>
                        {KNOWN_EXCHANGES.map((ex) => (
                          <option key={ex} value={ex}>
                            {ex}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="block space-y-1">
                      <span className="text-2xs uppercase tracking-wider text-fg-subtle flex items-center gap-1">
                        Stop Loss
                        {result.missing_fields.includes("stop_loss") && (
                          <Badge tone="warn">erforderlich</Badge>
                        )}
                      </span>
                      <input
                        type="text"
                        inputMode="decimal"
                        value={stopLossInput}
                        onChange={(e) => setStopLossInput(e.target.value)}
                        placeholder="z. B. 70400"
                        className="w-full rounded-sm border border-line bg-bg-0 px-2 py-1 font-mono text-xs text-fg focus:outline-none focus:ring-2 focus:ring-accent/40"
                      />
                    </label>
                    <label className="block space-y-1">
                      <span className="text-2xs uppercase tracking-wider text-fg-subtle flex items-center gap-1">
                        Targets (kommagetrennt)
                        {result.missing_fields.includes("targets") && (
                          <Badge tone="warn">erforderlich</Badge>
                        )}
                      </span>
                      <input
                        type="text"
                        value={targetsInput}
                        onChange={(e) => setTargetsInput(e.target.value)}
                        placeholder="z. B. 70965, 71200, 71400"
                        className="w-full rounded-sm border border-line bg-bg-0 px-2 py-1 font-mono text-xs text-fg focus:outline-none focus:ring-2 focus:ring-accent/40"
                      />
                    </label>
                    <label className="block space-y-1">
                      <span className="text-2xs uppercase tracking-wider text-fg-subtle flex items-center gap-1">
                        Leverage (x)
                        {result.missing_fields.includes("leverage") && (
                          <Badge tone="warn">erforderlich</Badge>
                        )}
                      </span>
                      <input
                        type="number"
                        min={1}
                        max={125}
                        value={leverageInput}
                        onChange={(e) => setLeverageInput(e.target.value)}
                        placeholder="z. B. 10"
                        className="w-full rounded-sm border border-line bg-bg-0 px-2 py-1 font-mono text-xs text-fg focus:outline-none focus:ring-2 focus:ring-accent/40"
                      />
                    </label>
                  </div>
                  <Button
                    variant="primary"
                    size="md"
                    onClick={submitWithCompletion}
                    disabled={busy}
                    className="glow-info"
                  >
                    {busy ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Send className="h-3.5 w-3.5" />
                    )}
                    Mit Ergänzung absenden
                  </Button>
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

              {(result.envelope_id || result.idempotency_key) && (
                <details className="rounded-sm border border-line-subtle bg-bg-2/30">
                  <summary className="cursor-pointer list-none px-2 py-1.5 text-2xs uppercase tracking-wider text-fg-subtle font-semibold flex items-center justify-between">
                    <span className="inline-flex items-center gap-1.5">
                      Technische Details
                      <InfoHint
                        label="Envelope und Idempotency"
                        hint={TERM_EXPLAIN.envelope + " " + TERM_EXPLAIN.idempotency}
                      />
                    </span>
                    <span className="text-2xs text-fg-subtle/70">(forensik)</span>
                  </summary>
                  <div className="px-2 pb-2 pt-1 space-y-1.5">
                    {result.envelope_id && (
                      <div className="space-y-0.5">
                        <div className="text-2xs uppercase tracking-wider text-fg-subtle">
                          Envelope-ID
                        </div>
                        <div className="font-mono text-2xs text-fg-muted break-all">
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
                  </div>
                </details>
              )}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function SignalDetailRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex gap-2 min-w-0">
      <span className="text-2xs uppercase tracking-wider text-fg-subtle shrink-0 w-[110px] pt-0.5">
        {label}
      </span>
      <span
        className={cn(
          "text-xs text-fg break-words min-w-0",
          mono && "font-mono",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function SignalDetails({
  signal,
  duplicate,
}: {
  signal: SignalSummary;
  duplicate: boolean;
}) {
  const exchanges =
    signal.exchange_scope.length > 0 ? signal.exchange_scope.join(", ") : "—";
  const targets =
    signal.targets.length > 0 ? signal.targets.map(formatNumber).join(", ") : "—";
  const statusLabel = (signal.signal_status ?? "—").toUpperCase();
  const duplicateLabel = duplicate ? "Ja — Wiederholung blockiert" : "Nein";

  return (
    <div className="rounded-sm border border-line-subtle bg-bg-2/40 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
          Was KAI erkannt hat
        </div>
        {signal.signal_id && (
          <span className="text-2xs font-mono text-fg-subtle break-all">
            {signal.signal_id}
          </span>
        )}
      </div>
      <div className="grid gap-x-4 gap-y-1.5 sm:grid-cols-2">
        <SignalDetailRow label="Symbol" value={signal.symbol ?? "—"} mono />
        <SignalDetailRow label="Direction" value={formatDirection(signal)} />
        <SignalDetailRow label="Exchange" value={exchanges} mono />
        <SignalDetailRow label="Market" value={signal.market_type ?? "—"} />
        <SignalDetailRow label="Entry Rule" value={formatEntryRule(signal)} mono />
        <SignalDetailRow label="Targets" value={targets} mono />
        <SignalDetailRow label="Stop Loss" value={formatNumber(signal.stop_loss)} mono />
        <SignalDetailRow label="Leverage" value={formatLeverage(signal.leverage)} />
        <SignalDetailRow label="Status" value={statusLabel} />
        <SignalDetailRow
          label="Timestamp"
          value={signal.signal_timestamp ? formatTs(signal.signal_timestamp) : "—"}
          mono
        />
        <SignalDetailRow label="Duplikat" value={duplicateLabel} />
      </div>
    </div>
  );
}

function EnvelopeCard({ rec }: { rec: EnvelopeRecord }) {
  const tone = envelopeTone(rec);
  const isDuplicate = rec.status === "duplicate";
  const router = useRouter();

  // DALI-P-103: Trail-Deep-Link aus Signal-Feed.
  // sessionStorage statt URL-Hash, weil der KAI-Router (state/Router.tsx)
  // bereits Hash-basiert ist und "#trail-XYZ" mit den Route-Namen kollidieren würde.
  // PremiumSignalTrail liest den Token beim ersten ready-Poll, scrollt, highlightet
  // 1.5s und löscht den Token. Falls envelope_id nicht im Trail-Fenster ist,
  // erscheint ein Hinweis-Banner statt eines stillen Fehlers.
  const handleTrailJump = () => {
    if (!rec.envelope_id) return;
    try {
      window.sessionStorage.setItem("kai.trail.target", rec.envelope_id);
    } catch {
      // privacy mode / quota — Navigation trotzdem ausführen,
      // der Trail rendert ohne Highlight.
    }
    router.navigate("portfolio");
  };

  return (
    <div
      className={cn(
        "rounded-sm border border-line bg-bg-0 p-4 border-l-4 space-y-3 transition-colors hover:bg-bg-2/40",
        TONE_ACCENT[tone],
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="shrink-0">{TONE_ICON[tone]}</span>
          <div className="min-w-0 space-y-1">
            <div className="text-sm font-semibold text-fg">
              {statusHeadline(rec)}
            </div>
            {rec.signal && (
              <div className="text-sm font-mono text-fg break-words">
                {signalHeadline(rec.signal)}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={tone === "neutral" ? "muted" : tone} dot>
                {rec.status ?? "—"}
              </Badge>
              <Badge tone="muted">
                {humanizeStage(rec.stage)}
              </Badge>
              {rec.message_type && (
                <Badge tone="info">{rec.message_type}</Badge>
              )}
              {rec.source && (
                <span className="text-2xs uppercase tracking-wider text-fg-subtle">
                  Quelle:{" "}
                  <span className="text-fg-muted">{humanizeSource(rec.source)}</span>
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="text-right shrink-0 flex flex-col items-end gap-1">
          <div className="text-2xs font-mono text-fg-muted whitespace-nowrap">
            {formatTs(rec.timestamp_utc)}
          </div>
          <div className="text-2xs text-fg-subtle">
            {relativeTs(rec.timestamp_utc)}
          </div>
          {rec.envelope_id && (
            <button
              type="button"
              onClick={handleTrailJump}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-sm border border-line-subtle bg-bg-2 text-2xs text-fg-muted hover:text-fg hover:bg-bg-3 hover:border-ai/40 transition-colors"
              title={`Im Portfolio-Trail anzeigen (envelope ${rec.envelope_id.slice(0, 8)}…)`}
            >
              <Target size={10} />
              <span>Trail</span>
            </button>
          )}
        </div>
      </div>

      {rec.raw_text_preview && (
        <details className="rounded-sm border border-line-subtle bg-bg-2/40 group" open>
          <summary className="cursor-pointer list-none px-3 py-2 text-2xs uppercase tracking-wider text-fg-subtle font-semibold flex items-center justify-between">
            <span>Original-Nachricht</span>
            <span className="text-2xs text-fg-subtle group-open:hidden">
              (klicken zum Öffnen)
            </span>
            <span className="text-2xs text-fg-subtle hidden group-open:inline">
              (klicken zum Schließen)
            </span>
          </summary>
          <pre className="px-3 pb-3 pt-1 font-mono text-2xs text-fg-muted whitespace-pre-wrap break-words leading-relaxed">
{rec.raw_text_preview}
          </pre>
        </details>
      )}

      {rec.signal && <SignalDetails signal={rec.signal} duplicate={isDuplicate} />}

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

      {/* DALI-T7: forensische Felder ins Technische-Details-Collapse. */}
      {(rec.envelope_id || rec.idempotency_key) && (
        <details className="rounded-sm border border-line-subtle bg-bg-2/30">
          <summary className="cursor-pointer list-none px-2.5 py-1.5 text-2xs uppercase tracking-wider text-fg-subtle font-semibold flex items-center justify-between">
            <span>Technische Details</span>
            <span className="text-2xs text-fg-subtle/70">(forensik)</span>
          </summary>
          <div className="px-2.5 pb-2 pt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-fg-subtle font-mono">
            {rec.envelope_id && (
              <span className="break-all">
                <span className="opacity-70">env:</span> {rec.envelope_id}
              </span>
            )}
            {rec.idempotency_key && (
              <span className="break-all">
                <span className="opacity-70">idem:</span> {rec.idempotency_key}
              </span>
            )}
          </div>
        </details>
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
        title={
          <span className="inline-flex items-center gap-2">
            Letzte empfangene externe Signale
            <InfoHint
              label="Audit-Log"
              hint={TERM_EXPLAIN.audit_log}
            />
          </span>
        }
        subtitle="Zuletzt verarbeitete Signale aus Telegram, Dashboard-Eingaben, externen Schnittstellen und API-Feeds. Neueste zuerst."
        right={
          <div className="flex items-center gap-2">
            {state.state === "ready" && (
              <Badge tone="muted">{count} Einträge</Badge>
            )}
            <Button variant="outline" size="sm" onClick={state.reload}>
              <RefreshCw className="h-3.5 w-3.5" />
              Aktualisieren
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
        <div className="rounded-sm border border-dashed border-line-subtle bg-bg-0 p-6 text-center text-xs text-fg-subtle space-y-1.5">
          <Inbox className="h-6 w-6 mx-auto mb-1 opacity-50" />
          <div className="text-fg font-medium">
            Noch keine externen Signale empfangen
          </div>
          <div>
            Sobald du oben ein Signal einfügst oder Telegram eines weiterleitet,
            erscheint es hier.
          </div>
        </div>
      )}
      {state.state === "ready" && records.length > 0 && (
        <div className="space-y-2.5">
          {records.map((rec, i) => (
            <EnvelopeCard
              key={(rec.envelope_id ?? "na") + "-" + (rec.timestamp_utc ?? i) + "-" + i}
              rec={rec}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

// DALI-T7: Triage-Strip — 4 Hero-Kpis "heute" — visueller Anker für den
// Operator, sofort erkennbar wie viel heute lief und wie es geendet ist.
function TriageStrip({ records }: { records: EnvelopeRecord[] }) {
  const counts = useMemo(() => todayCounts(records), [records]);
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <Kpi
        label="Heute akzeptiert"
        value={counts.accepted}
        sub="erfolgreich verarbeitet"
        tone="pos"
        size="lg"
      />
      <Kpi
        label="Heute Duplikate"
        value={counts.duplicate}
        sub="bereits empfangen"
        tone="warn"
        size="lg"
      />
      <Kpi
        label="Ergänzung nötig"
        value={counts.needs}
        sub="wartet auf Operator"
        tone="info"
        size="lg"
      />
      <Kpi
        label="Heute abgelehnt"
        value={counts.rejected}
        sub="siehe Fehler"
        tone="neg"
        size="lg"
      />
    </div>
  );
}

export function ExternalSignalsPage() {
  const { t } = useT();
  const auditState = useApi<EnvelopeRecentResponse>(
    (signal) => fetchRecentEnvelopes(50, signal),
    30_000,
  );

  const records = auditState.state === "ready" ? auditState.data.records : [];

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.external.title")}
        sub="Trading-Signale aus Telegram, externen Gruppen, manuellen Quellen oder APIs einfügen — KAI analysiert, validiert und dokumentiert."
        tone="accent"
        icon={<Upload size={18} />}
        divider={false}
      />

      <TriageStrip records={records} />

      <SignalPasteForm onPasted={auditState.reload} />
      <AuditLogPanel state={auditState} />
    </div>
  );
}
