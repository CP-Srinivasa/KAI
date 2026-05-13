import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/* DALI Dashboard v2 — Master-Spec G5: Status-Spiegel
   Konsistente Statusbadges fuer "Welcher Datenmodus ist das hier?" -
   4 Modi: live, paper (Simuliert), prepared (Vorbereitet), planned (Geplant).

   Toene konsistent zu kai.tokens.css:
   - live      -> info (Cyan/Blau), pulsing dot
   - paper     -> warn (Orange),    statischer dot
   - prepared  -> fg-subtle,        gestreifter dot
   - planned   -> ai (Violett),     Strichlinie

   Doppelt zu Icon/Farbe gibt es immer ein Klartext-Wort - kein
   reines Farbsignal (A11y, Color-Blindness, Print). */

export type StatusBadgeMode = "live" | "paper" | "prepared" | "planned";

const LABEL: Record<StatusBadgeMode, string> = {
  live: "Live",
  paper: "Paper",
  prepared: "Vorbereitet",
  planned: "Geplant",
};

const DESCRIPTION: Record<StatusBadgeMode, string> = {
  live: "Echte Live-Daten aus aktiven Quellen.",
  paper: "Simulierte Daten - kein echtes Trading.",
  prepared: "Backend ist verdrahtet, Daten kommen bald.",
  planned: "In Planung - noch nicht implementiert.",
};

type Props = {
  mode: StatusBadgeMode;
  /** Optional Override fuer Label-Text. */
  label?: string;
  /** Compact 'sm' fuer Inline-Use, 'md' fuer Headers (default). */
  size?: "sm" | "md";
  /** Optionaler Untertitel/Tooltipp. Wenn nicht gesetzt: DESCRIPTION[mode]. */
  title?: string;
  className?: string;
};

export function StatusBadge({ mode, label, size = "md", title, className }: Props): ReactNode {
  const text = label ?? LABEL[mode];
  const tip = title ?? DESCRIPTION[mode];

  const baseToneCls: Record<StatusBadgeMode, string> = {
    live: "border-info/45 bg-info/10 text-info",
    paper: "border-warn/45 bg-warn/10 text-warn",
    prepared: "border-fg-subtle/40 bg-bg-2 text-fg-muted",
    planned: "border-ai/45 bg-ai/10 text-ai",
  };

  const dotCls: Record<StatusBadgeMode, string> = {
    live: "bg-info animate-pulse motion-reduce:animate-none",
    paper: "bg-warn",
    prepared:
      "bg-transparent border border-fg-subtle/60 [background-image:repeating-linear-gradient(45deg,transparent_0,transparent_2px,currentColor_2px,currentColor_3px)]",
    planned: "bg-transparent border border-dashed border-ai",
  };

  const sizeCls = size === "sm" ? "h-5 px-1.5 text-2xs gap-1" : "h-6 px-2 text-xs gap-1.5";
  const dotSize = size === "sm" ? "w-1.5 h-1.5" : "w-2 h-2";

  return (
    <span
      role="status"
      title={tip}
      aria-label={`${text} - ${tip}`}
      className={cn(
        "inline-flex items-center rounded-sm border font-mono uppercase tracking-wider select-none",
        sizeCls,
        baseToneCls[mode],
        className,
      )}
    >
      <span aria-hidden className={cn("inline-block rounded-full shrink-0", dotSize, dotCls[mode])} />
      <span>{text}</span>
    </span>
  );
}
