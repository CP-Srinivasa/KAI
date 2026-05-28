import {
  AlertTriangle,
  Check,
  Circle,
  Clock,
  HelpCircle,
  Minus,
  X,
} from "lucide-react";
import { Badge, ProgressBar, SectionLabel } from "@/components/ui/Primitives";
import type { PremiumSignalAnalytics, PremiumSignalTargetStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * PremiumSignalAnalytics — operatorzentrierte Auswertungs-Kacheln pro Signal.
 *
 * 2026-05-28 /goal-Sprint. Macht "auf einen Blick" sichtbar, was bisher in
 * Logs/Rohdaten verborgen war: eingesetztes Kapital + Anteil, Ergebnis,
 * Target-Erreichung, Entry-Qualität, Quellen-Qualität, Optimierungs-Hinweise.
 *
 * Datenquelle: `analytics`-Block aus `/api/premium-signals/trail`
 * (Backend: app/observability/premium_signal_analytics.py).
 *
 * Robustheit: alle Felder sind null-tolerant. Fehlt eine belastbare Basis,
 * zeigt die UI "nicht verfügbar" / "nicht bewertbar" statt einen Wert zu raten.
 */

type Tone = "pos" | "neg" | "warn" | "info" | "muted" | "ai";

// ── Formatter ────────────────────────────────────────────────────────────────

function fmtUsd(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toLocaleString("de-DE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} USD`;
}

function fmtUsdPlain(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v.toLocaleString("de-DE", { maximumFractionDigits: 2 })} USD`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toLocaleString("de-DE", { maximumFractionDigits: 1 })} %`;
}

function fmtPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1000) return v.toLocaleString("de-DE", { maximumFractionDigits: 2 });
  if (Math.abs(v) >= 1) return v.toFixed(4);
  return v.toFixed(6);
}

// ── Status-Maps ──────────────────────────────────────────────────────────────

const RESULT_META: Record<string, { label: string; tone: Tone }> = {
  win: { label: "Gewinn", tone: "pos" },
  loss: { label: "Verlust", tone: "neg" },
  break_even: { label: "Break-even", tone: "muted" },
  open: { label: "Offen", tone: "info" },
  cancelled: { label: "Abgebrochen", tone: "muted" },
  unknown: { label: "Unbekannt", tone: "muted" },
};

const ENTRY_META: Record<
  string,
  { label: string; tone: Tone; Icon: typeof Check }
> = {
  entered_on_time: { label: "Rechtzeitig", tone: "pos", Icon: Check },
  waited_for_entry: { label: "Gewartet", tone: "info", Icon: Clock },
  entered_late: { label: "Verspätet", tone: "warn", Icon: Clock },
  missed_entry: { label: "Verfehlt", tone: "neg", Icon: X },
  unknown: { label: "Unbekannt", tone: "muted", Icon: Minus },
};

const SOURCE_META: Record<string, { label: string; tone: Tone }> = {
  good: { label: "Gut", tone: "pos" },
  medium: { label: "Mittel", tone: "warn" },
  weak: { label: "Schwach", tone: "neg" },
  unknown: { label: "Nicht bewertbar", tone: "muted" },
};

const TARGET_META: Record<
  PremiumSignalTargetStatus["status"],
  { label: string; dot: string; ring: string; text: string }
> = {
  hit: { label: "erreicht", dot: "bg-pos", ring: "border-pos/40", text: "text-pos" },
  missed: { label: "verfehlt", dot: "bg-neg", ring: "border-neg/40", text: "text-neg" },
  pending: { label: "offen", dot: "bg-info", ring: "border-info/40", text: "text-info" },
  skipped: { label: "übersprungen", dot: "bg-warn", ring: "border-warn/40", text: "text-warn" },
  unknown: {
    label: "unbewertbar",
    dot: "bg-fg-subtle/60",
    ring: "border-line-subtle",
    text: "text-fg-muted",
  },
};

// ── kleine Tile-Hülle ────────────────────────────────────────────────────────

function Tile({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <div className={cn("rounded-md border border-line-subtle bg-bg-1/60 p-2.5", className)}>
      <SectionLabel className="mb-1.5">{label}</SectionLabel>
      {children}
    </div>
  );
}

// ── Kacheln ──────────────────────────────────────────────────────────────────

function CapitalTile({ a }: { a: PremiumSignalAnalytics }): JSX.Element {
  const pct = a.invested_capital_pct;
  const hasBase = a.available_capital_at_entry != null && pct != null;
  // Hoher Kapitalanteil = Risiko → Tone steigt mit %.
  const tone: "pos" | "warn" | "neg" | "muted" =
    pct == null ? "muted" : pct > 50 ? "neg" : pct > 25 ? "warn" : "pos";
  return (
    <Tile label="Kapital">
      <div className="font-mono text-sm font-semibold text-fg">
        {fmtUsdPlain(a.invested_capital)}
      </div>
      {hasBase ? (
        <>
          <div className="mt-1 flex items-center justify-between text-2xs font-mono text-fg-subtle">
            <span>{pct!.toLocaleString("de-DE", { maximumFractionDigits: 1 })}% vom Kapital</span>
            <span>{fmtUsdPlain(a.available_capital_at_entry)}</span>
          </div>
          <ProgressBar
            className="mt-1"
            value={pct}
            target={100}
            tone={tone}
            size="sm"
            label={`Kapitalanteil ${pct}%`}
          />
        </>
      ) : (
        <div className="mt-1 text-2xs text-fg-muted">
          {a.invested_capital == null
            ? "kein Einstieg / keine Daten"
            : "Kapitalbasis nicht verfügbar"}
        </div>
      )}
    </Tile>
  );
}

function ResultTile({ a }: { a: PremiumSignalAnalytics }): JSX.Element {
  const meta = RESULT_META[a.trade_result_status] ?? RESULT_META.unknown;
  const pnl = a.final_pnl_usd;
  const pnlTone =
    pnl == null ? "text-fg-muted" : pnl > 0 ? "text-pos" : pnl < 0 ? "text-neg" : "text-fg-muted";
  const derived = a.final_pnl_source === "fills";
  return (
    <Tile label="Ergebnis">
      <div className="flex items-center justify-between gap-1">
        <span
          className={cn("font-mono text-sm font-semibold", pnlTone)}
          title={derived ? "PnL aus Fill-Preisen berechnet (Engine-Wert fehlte)" : undefined}
        >
          {derived && pnl != null ? "≈ " : ""}
          {fmtUsd(pnl)}
        </span>
        <Badge tone={meta.tone}>{meta.label}</Badge>
      </div>
      <div className="mt-1 text-2xs font-mono text-fg-subtle">
        {a.final_pnl_pct != null ? `${fmtPct(a.final_pnl_pct)} auf Einsatz` : "kein PnL-Wert"}
      </div>
    </Tile>
  );
}

function EntryTile({ a }: { a: PremiumSignalAnalytics }): JSX.Element {
  const meta = ENTRY_META[a.entry_status] ?? ENTRY_META.unknown;
  const Icon = meta.Icon;
  return (
    <Tile label="Einstieg">
      <div className="flex items-center justify-between gap-1">
        <Badge tone={meta.tone}>
          <Icon size={10} />
          {meta.label}
        </Badge>
        <span className="text-2xs font-mono text-fg-subtle">{a.entry_delay_label}</span>
      </div>
      <div className="mt-1 text-2xs font-mono text-fg-subtle">
        <span className="text-fg-muted">Ist </span>
        {fmtPrice(a.actual_entry_price)}
        <span className="text-fg-muted"> · Plan </span>
        {fmtPrice(a.planned_entry_value)}
      </div>
    </Tile>
  );
}

function SourceTile({ a }: { a: PremiumSignalAnalytics }): JSX.Element {
  const meta = SOURCE_META[a.source_quality_status] ?? SOURCE_META.unknown;
  return (
    <Tile label="Quelle">
      <div className="flex items-center justify-between gap-1">
        <span
          className="font-mono text-2xs font-semibold text-fg truncate"
          title={a.source_name ?? undefined}
        >
          {a.source_name ?? "—"}
        </span>
        <Badge tone={meta.tone}>{meta.label}</Badge>
      </div>
      <div className="mt-1 text-2xs text-fg-subtle leading-snug">{a.source_quality_reason}</div>
    </Tile>
  );
}

// ── Target-Stepper ───────────────────────────────────────────────────────────

function TargetStepper({
  targets,
}: {
  targets: PremiumSignalTargetStatus[];
}): JSX.Element | null {
  if (!targets || targets.length === 0) return null;
  return (
    <div className="mt-2">
      <SectionLabel className="mb-1.5">Targets</SectionLabel>
      <div className="flex items-stretch gap-1 flex-wrap">
        {targets.map((t, i) => {
          const meta = TARGET_META[t.status] ?? TARGET_META.unknown;
          const Icon =
            t.status === "hit"
              ? Check
              : t.status === "missed"
                ? X
                : t.status === "pending"
                  ? Circle
                  : t.status === "skipped"
                    ? Minus
                    : HelpCircle;
          return (
            <div key={`${t.target_number}-${i}`} className="flex items-center">
              <div
                className={cn(
                  "flex flex-col items-center justify-center rounded-md border px-2 py-1 min-w-[58px]",
                  "bg-bg-1/60",
                  meta.ring,
                )}
                title={`TP${t.target_number} ${fmtPrice(t.target_price)} — ${meta.label}`}
              >
                <div className={cn("flex items-center gap-1 text-2xs font-semibold", meta.text)}>
                  <Icon size={10} />
                  TP{t.target_number}
                </div>
                <div className="font-mono text-2xs text-fg-subtle">{fmtPrice(t.target_price)}</div>
              </div>
              {i < targets.length - 1 && (
                <div className="w-2 h-px bg-line-subtle/60 mx-0.5" aria-hidden />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Analyse-Hinweise ─────────────────────────────────────────────────────────

function AnalysisHints({ hints }: { hints: string[] }): JSX.Element | null {
  if (!hints || hints.length === 0) return null;
  return (
    <div className="mt-2 space-y-1">
      {hints.map((h, i) => (
        <div key={i} className="flex items-start gap-1.5 text-2xs text-fg-muted">
          <AlertTriangle size={11} className="text-warn shrink-0 mt-0.5" />
          <span className="leading-snug">{h}</span>
        </div>
      ))}
    </div>
  );
}

// ── Public-Block ─────────────────────────────────────────────────────────────

export function SignalAnalyticsBlock({
  analytics,
}: {
  analytics: PremiumSignalAnalytics;
}): JSX.Element {
  return (
    <div className="mt-2 pt-2 border-t border-line-subtle/40">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <CapitalTile a={analytics} />
        <ResultTile a={analytics} />
        <EntryTile a={analytics} />
        <SourceTile a={analytics} />
      </div>
      <TargetStepper targets={analytics.targets} />
      <AnalysisHints hints={analytics.analysis_hints} />
    </div>
  );
}
