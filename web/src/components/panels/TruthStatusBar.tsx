import { memo, useMemo, useState } from "react";
import { ShieldCheck, ChevronDown, ChevronUp, Activity } from "lucide-react";
import { Card, InfoHint } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type {
  DashboardQuality,
  DashboardRegime,
  PriorityGateSummary,
} from "@/lib/api";
import {
  deriveTruthChips,
  highestTruthTone,
  type TruthChip,
  type TruthTone,
} from "@/lib/truthStatus";
import { staleStatusLabel } from "@/lib/labels";

// 2026-06-04 DALI Truth-Visibility-Sprint: kompakte Neon-Statusleiste, die die
// heute erarbeiteten Truth-Layer-Zustaende auf einen Blick sichtbar macht.
// Keine neue Layout-DNA — eine schmale Card im bestehenden Cyberpunk-Stil.

const TONE_TEXT: Record<TruthTone, string> = {
  critical: "text-neg",
  warn: "text-warn",
  info: "text-info",
  readonly: "text-fg-muted",
  ok: "text-pos",
  muted: "text-fg-subtle",
};

const TONE_DOT: Record<TruthTone, string> = {
  critical: "bg-neg",
  warn: "bg-warn",
  info: "bg-info",
  readonly: "bg-info/60",
  ok: "bg-pos",
  muted: "bg-fg-subtle/50",
};

const TONE_GLOW: Record<TruthTone, string> = {
  critical: "glow-neg",
  warn: "glow-warn",
  info: "glow-info",
  readonly: "",
  ok: "glow-pos",
  muted: "",
};

const TONE_BORDER: Record<TruthTone, string> = {
  critical: "border-neg/40 bg-neg/5",
  warn: "border-warn/40 bg-warn/5",
  info: "border-info/30 bg-info/5",
  readonly: "border-line-subtle bg-bg-2/40",
  ok: "border-pos/30 bg-pos/5",
  muted: "border-line-subtle bg-bg-2/40",
};

function ChipPill({ chip }: { chip: TruthChip }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border px-2 py-1",
        TONE_BORDER[chip.tone],
      )}
      title={`${chip.label}: ${chip.value} — ${chip.hint}`}
      role="status"
    >
      <span
        className={cn(
          "h-1.5 w-1.5 shrink-0 rounded-full",
          TONE_DOT[chip.tone],
          TONE_GLOW[chip.tone],
        )}
        aria-hidden
      />
      <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
        {chip.label}
      </span>
      <span className={cn("text-2xs font-mono font-semibold", TONE_TEXT[chip.tone])}>
        {chip.value}
      </span>
    </span>
  );
}

type Props = {
  quality: DashboardQuality | null;
  regime: DashboardRegime | null;
  priorityGate: PriorityGateSummary | null;
  qualityState: "loading" | "ready" | "error";
};

function TruthStatusBarImpl({ quality, regime, priorityGate, qualityState }: Props) {
  const [showDiag, setShowDiag] = useState(false);
  const chips = useMemo(
    () => deriveTruthChips(quality, regime, priorityGate),
    [quality, regime, priorityGate],
  );
  const worst = highestTruthTone(chips);
  const headerDot = cn("h-2 w-2 rounded-full", TONE_DOT[worst], TONE_GLOW[worst]);

  const buildHash =
    (import.meta.env.VITE_BUILD_HASH as string | undefined) ?? "dev";
  const reportTs = quality?.generated_at
    ? quality.generated_at.substring(0, 19).replace("T", " ")
    : qualityState === "error"
      ? "Endpoint-Fehler"
      : "lädt …";
  const paperStale = staleStatusLabel(quality?.paper_evidence?.stale_status);
  const lifetimeFills = quality?.paper_evidence?.fills_total ?? quality?.paper_fills_with_pnl ?? 0;
  const recentFills = quality?.paper_evidence?.fills_recent_24h ?? 0;
  const contractVersion = quality?.dashboard_truth_contract_version ?? null;

  return (
    <Card className="px-4 py-3" padded={false}>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 shrink-0">
          <span className={headerDot} aria-hidden />
          <ShieldCheck size={14} className="text-fg-muted" aria-hidden />
          <h2 className="text-2xs font-semibold uppercase tracking-[0.12em] text-fg-muted">
            Wahrheitsstatus
          </h2>
          <InfoHint
            label="Wahrheitsstatus"
            hint="Verdichtete Truth-Layer-Zustaende: was ist historisch, was 24h-aktuell, was read-only, was blockiert, was unbewiesen. Read-only beeinflusst keine Trades."
          />
        </div>
        <button
          type="button"
          onClick={() => setShowDiag((s) => !s)}
          aria-expanded={showDiag}
          className="inline-flex items-center gap-1 text-2xs font-mono text-fg-subtle hover:text-fg transition-colors focus:outline-none focus-visible:underline"
        >
          <Activity size={11} aria-hidden />
          Warum unverändert?
          {showDiag ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        </button>
      </div>

      <div className="mt-2.5 flex items-center gap-1.5 flex-wrap">
        {chips.map((chip) => (
          <ChipPill key={chip.key} chip={chip} />
        ))}
      </div>

      {showDiag && (
        <div className="mt-3 pt-3 border-t border-line-subtle grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-2xs font-mono text-fg-subtle">
          <DiagRow label="Truth-Contract" value={contractVersion != null ? `v${contractVersion}` : "nicht instrumentiert"} />
          <DiagRow label="Report-Stand" value={reportTs} />
          <DiagRow label="Build-Hash" value={buildHash} />
          <DiagRow label="Paper-Frische" value={paperStale} />
          <DiagRow
            label="Fills hist / 24h"
            value={`${lifetimeFills} / ${recentFills}`}
            warn={lifetimeFills > 0 && recentFills === 0}
          />
          <DiagRow
            label="Hinweis"
            value={
              lifetimeFills > 0 && recentFills === 0
                ? "historische Evidence ≠ 24h-Fortschritt"
                : "—"
            }
          />
        </div>
      )}
    </Card>
  );
}

function DiagRow({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-fg-subtle/80">{label}</span>
      <span className={cn("text-fg-muted", warn && "text-warn")}>{value}</span>
    </div>
  );
}

export const TruthStatusBar = memo(TruthStatusBarImpl);
