// KAI Live Widget — central system persona render.
// DALI Audit Layout: Hero-Strip oben (Full), Header-Anchor (Compact), Top-Stack vor KPI (Mobile).
// ERROR/WARNING brechen visuell durch — IDLE/ANALYSIS bleiben unauffällig.

import { useMemo } from "react";
import { cn } from "@/lib/utils";
import type { KaiLiveWidgetProps } from "../../kai/types";
import { KaiAvatar } from "./KaiAvatar";
import { KaiStatusBadge } from "./KaiStatusBadge";

const LANG_LOCALE = { de: "de-DE", en: "en-US" } as const;

function formatTs(ts: string, language: "de" | "en"): string {
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toLocaleString(LANG_LOCALE[language], {
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  } catch {
    return ts;
  }
}

export function KaiLiveWidget(props: KaiLiveWidgetProps) {
  const {
    runtimeState,
    lastSignal,
    lastWarning,
    agentStatuses = [],
    compact = false,
    language = "de",
    onOpenAuditLog,
    onOpenDetails,
  } = props;

  const ts = useMemo(() => formatTs(runtimeState.timestamp, language), [runtimeState.timestamp, language]);

  if (compact) {
    return (
      <div
        className={cn(
          "kai-widget kai-widget--compact",
          `kai-widget--state-${runtimeState.state}`,
          "flex items-center gap-2",
        )}
        aria-label="KAI compact"
      >
        <KaiAvatar state={runtimeState.state} size="compact" />
        <KaiStatusBadge state={runtimeState.state} />
      </div>
    );
  }

  return (
    <section
      className={cn(
        "kai-widget kai-widget--full kai-card",
        `kai-widget--state-${runtimeState.state}`,
        "p-4",
      )}
      aria-label="KAI Live Widget"
    >
      <header className="flex items-center justify-between gap-4 mb-3">
        <div className="flex items-center gap-3">
          <KaiAvatar state={runtimeState.state} size="full" />
          <div>
            <div className="text-sm uppercase tracking-widest font-bold text-fg">KAI LIVE</div>
            <div className="text-xs text-fg-subtle italic">Persona non grata</div>
          </div>
        </div>
        <KaiStatusBadge state={runtimeState.state} />
      </header>

      <div className="kai-widget__body space-y-2">
        <p className="text-sm leading-relaxed text-fg">
          „<span className="kai-comment-text">{runtimeState.comment}</span>"
        </p>
        <p className="text-xs text-fg-subtle font-mono">{ts}</p>

        {lastSignal && (
          <div className="kai-mini-card kai-mini-card--signal mt-3 p-2 rounded-md border border-fg-subtle/30 text-xs">
            <strong className="text-fg-muted">Last Signal: </strong>
            <span className="font-mono">
              {lastSignal.asset} · {lastSignal.direction} · {lastSignal.confidence}% · Risk {lastSignal.risk}
            </span>
          </div>
        )}

        {lastWarning && (
          <div className="kai-mini-card kai-mini-card--warning mt-2 p-2 rounded-md border border-fg-subtle/30 text-xs">
            <strong className="text-fg-muted">Last Warning: </strong>
            <span>
              {lastWarning.target} · {lastWarning.risk} · {lastWarning.problem}
            </span>
          </div>
        )}

        {agentStatuses.length > 0 && (
          <div className="kai-agent-row flex flex-wrap gap-1.5 mt-3">
            {agentStatuses.slice(0, 6).map((agent) => (
              <span
                key={agent.agent}
                className={cn(
                  "px-1.5 py-0.5 text-[10px] uppercase tracking-wide font-mono rounded-sm border",
                  agent.status === "OK" && "border-pos/40 text-pos",
                  agent.status === "WARNING" && "border-warn/40 text-warn",
                  agent.status === "ERROR" && "border-neg/40 text-neg",
                  agent.status === "OFFLINE" && "border-fg-subtle/30 text-fg-subtle",
                  agent.status === "UNKNOWN" && "border-fg-subtle/30 text-fg-subtle italic",
                )}
                title={agent.summary}
              >
                {agent.agent}: {agent.status}
              </span>
            ))}
          </div>
        )}
      </div>

      <footer className="mt-3 flex items-center justify-between gap-2">
        {runtimeState.nextAction && (
          <span className="text-xs text-fg-muted">{runtimeState.nextAction}</span>
        )}
        <div className="flex gap-2 ml-auto">
          <button
            type="button"
            onClick={onOpenDetails ?? (() => undefined)}
            disabled={!onOpenDetails}
            className="text-xs px-2 py-1 rounded-md border border-fg-subtle/30 hover:border-fg-subtle text-fg-muted hover:text-fg disabled:opacity-50 disabled:cursor-not-allowed"
            title={onOpenDetails ? "Details öffnen" : "Details (Phase 2)"}
          >
            Details
          </button>
          <button
            type="button"
            onClick={onOpenAuditLog ?? (() => undefined)}
            disabled={!onOpenAuditLog}
            className="text-xs px-2 py-1 rounded-md border border-fg-subtle/30 hover:border-fg-subtle text-fg-muted hover:text-fg disabled:opacity-50 disabled:cursor-not-allowed"
            title={onOpenAuditLog ? "Audit-Log öffnen" : "Audit (Phase 2)"}
          >
            Audit
          </button>
        </div>
      </footer>
    </section>
  );
}
