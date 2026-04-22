import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/* ---------- Card / Panel ---------- */

type CardProps = HTMLAttributes<HTMLDivElement> & {
  as?: "div" | "section" | "article";
  padded?: boolean;
};

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { className, padded = true, children, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      className={cn(
        "rounded-lg border border-line-subtle bg-bg-1 shadow-panel kai-fade",
        padded && "p-5",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
});

export function CardHeader({
  title,
  subtitle,
  right,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col sm:flex-row sm:items-start sm:justify-between gap-2 sm:gap-4 mb-4",
        className,
      )}
    >
      <div className="min-w-0">
        <h3 className="text-sm font-semibold tracking-tight text-fg break-words">{title}</h3>
        {subtitle && <p className="mt-0.5 text-xs text-fg-muted break-words">{subtitle}</p>}
      </div>
      {right && <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap sm:shrink-0">{right}</div>}
    </div>
  );
}

/* ---------- Badge ---------- */

// text-2xs (11px) Konvention (DALI-F-009):
// erlaubt für: font-mono Identifier/Timestamps, einzelne Badge-Labels,
// uppercase-tracked Micro-Labels. NICHT für mehrzeiligen Body-/Helper-Text
// oder Tabellen-Zellen — dort text-xs (12px) verwenden.
type BadgeTone = "neutral" | "pos" | "neg" | "warn" | "info" | "ai" | "muted";

export function Badge({
  tone = "neutral",
  children,
  className,
  dot = false,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
  dot?: boolean;
}) {
  const tones: Record<BadgeTone, string> = {
    neutral: "bg-bg-3 text-fg border-line",
    pos: "bg-pos/10 text-pos border-pos/20",
    neg: "bg-neg/10 text-neg border-neg/20",
    warn: "bg-warn/10 text-warn border-warn/20",
    info: "bg-info/10 text-info border-info/20",
    ai: "bg-ai/10 text-ai border-ai/25",
    muted: "bg-bg-2 text-fg-muted border-line-subtle",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-xs border px-1.5 py-0.5 text-2xs font-medium",
        tones[tone],
        className,
      )}
    >
      {dot && <span className={cn("h-1.5 w-1.5 rounded-full", toneDot(tone))} />}
      {children}
    </span>
  );
}

function toneDot(tone: BadgeTone) {
  switch (tone) {
    case "pos":
      return "bg-pos";
    case "neg":
      return "bg-neg";
    case "warn":
      return "bg-warn";
    case "info":
      return "bg-info";
    case "ai":
      return "bg-ai";
    case "muted":
      return "bg-fg-subtle";
    default:
      return "bg-fg";
  }
}

/* ---------- StatusDot ---------- */

export function StatusDot({
  tone = "neutral",
  pulse = false,
  className,
}: {
  tone?: BadgeTone;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span className={cn("relative inline-flex h-2 w-2", className)}>
      {pulse && (
        <span
          className={cn(
            "absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping",
            toneDot(tone),
          )}
        />
      )}
      <span className={cn("relative inline-flex rounded-full h-2 w-2", toneDot(tone))} />
    </span>
  );
}

/* ---------- Button ---------- */

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "outline";
  size?: "sm" | "md";
};

export const Button = forwardRef<HTMLButtonElement, BtnProps>(function Button(
  { variant = "outline", size = "md", className, children, ...rest },
  ref,
) {
  const base =
    "inline-flex items-center gap-2 rounded-sm font-medium transition-colors select-none focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:opacity-50 disabled:pointer-events-none";
  const sizes = {
    sm: "h-7 px-2.5 text-xs",
    md: "h-8 px-3 text-xs",
  }[size];
  const variants = {
    primary: "bg-accent text-white hover:bg-accent/90",
    outline: "border border-line bg-bg-1 text-fg hover:bg-bg-2",
    ghost: "text-fg-muted hover:bg-bg-2 hover:text-fg",
  }[variant];
  return (
    <button ref={ref} className={cn(base, sizes, variants, className)} {...rest}>
      {children}
    </button>
  );
});

/* ---------- Section label ---------- */

export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "text-2xs font-semibold uppercase tracking-[0.08em] text-fg-subtle",
        className,
      )}
    >
      {children}
    </div>
  );
}

/* ---------- ProgressBar (DALI-P-025) ----------
   Zentrale Progress-Komponente für alle Panels (Quality-Bar, Re-Entry-Gate,
   Active-Precision u.a.). Tokens vereinheitlicht (h-1.5 md / h-1 sm, rounded-full,
   bg-bg-3 track); Auto-Tone-Logik: >=100 pos, >=50 warn, sonst neg; bei
   sufficientSample=false oder value=null → muted. Per tone="pos|warn|neg|muted"
   explizit überschreibbar. A11y-Attribute (role, aria-valuenow/-min/-max,
   aria-label) werden zentral gesetzt. */

type ProgressTone = "pos" | "warn" | "neg" | "muted" | "auto";
type ProgressSize = "sm" | "md";

const PROGRESS_FILL: Record<Exclude<ProgressTone, "auto">, string> = {
  pos: "bg-pos",
  warn: "bg-warn",
  neg: "bg-neg",
  muted: "bg-fg-subtle/60",
};

export function ProgressBar({
  value,
  target,
  tone = "auto",
  size = "md",
  label,
  sufficientSample = true,
  className,
}: {
  value: number | null | undefined;
  target: number;
  tone?: ProgressTone;
  size?: ProgressSize;
  label: string;
  sufficientSample?: boolean;
  className?: string;
}) {
  const hasValue = value != null;
  const pct = hasValue ? Math.min(100, (value / target) * 100) : 0;

  const resolved: Exclude<ProgressTone, "auto"> =
    !hasValue || !sufficientSample
      ? "muted"
      : tone !== "auto"
        ? tone
        : pct >= 100
          ? "pos"
          : pct >= 50
            ? "warn"
            : "neg";

  const trackHeight = size === "sm" ? "h-1" : "h-1.5";

  return (
    <div
      className={cn(trackHeight, "w-full rounded-full bg-bg-3 overflow-hidden", className)}
      role="progressbar"
      aria-valuenow={hasValue ? Math.round(value) : 0}
      aria-valuemin={0}
      aria-valuemax={target}
      aria-label={label}
    >
      <div
        className={cn("h-full rounded-full transition-all", PROGRESS_FILL[resolved])}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
