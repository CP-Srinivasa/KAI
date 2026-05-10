import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

// 2026-05-08 Operator-Folge: Synthwave-Divider unter jedem PageHeader.
// 2026-05-10 DALI-A2: Tone+Icon-Variante. Jede Sub-Page bekommt eine
// semantische Tonart (info/ai/warn/neg/pos/accent), die ueber 2px-Bar
// links neben dem Title und Icon-Tint im Title-Cluster kommuniziert wird.
// Operator-Beschwerde: "alle Untermenues sehen tot/gleich aus" — Tone+Icon
// gibt jeder Page in <0.5s Identifikation, ohne Animation oder Overload.
export type PageHeaderTone = "info" | "ai" | "warn" | "neg" | "pos" | "accent";

const TONE_BAR: Record<PageHeaderTone, string> = {
  info: "bg-info",
  ai: "bg-ai",
  warn: "bg-warn",
  neg: "bg-neg",
  pos: "bg-pos",
  accent: "bg-accent",
};

const TONE_ICON: Record<PageHeaderTone, string> = {
  info: "text-info",
  ai: "text-ai",
  warn: "text-warn",
  neg: "text-neg",
  pos: "text-pos",
  accent: "text-accent",
};

export function PageHeader({
  title,
  sub,
  right,
  tone,
  icon,
}: {
  title: string;
  sub?: string;
  right?: ReactNode;
  tone?: PageHeaderTone;
  icon?: ReactNode;
}) {
  const barClass = tone ? TONE_BAR[tone] : "bg-fg-subtle/40";
  const iconClass = tone ? TONE_ICON[tone] : "text-fg-subtle";
  return (
    <div className="space-y-2.5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0 relative pl-3">
          {/* 2px-Tone-Bar links — Page-Identifikation */}
          <span
            aria-hidden="true"
            className={cn(
              "absolute left-0 top-1 bottom-1 w-[2px] rounded-full",
              barClass,
            )}
          />
          <h1 className="synthwave-title text-xl font-semibold tracking-tight text-fg flex items-center gap-2">
            {icon && (
              <span className={cn("shrink-0", iconClass)} aria-hidden="true">
                {icon}
              </span>
            )}
            {title}
          </h1>
          {sub && <p className="text-xs text-fg-muted mt-1 break-words">{sub}</p>}
        </div>
        {right}
      </div>
      <div className="synthwave-divider" aria-hidden="true" />
    </div>
  );
}
