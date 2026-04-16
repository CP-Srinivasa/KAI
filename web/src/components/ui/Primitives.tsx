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
    <div className={cn("flex items-start justify-between gap-4 mb-4", className)}>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold tracking-tight text-fg">{title}</h3>
        {subtitle && <p className="mt-0.5 text-xs text-fg-muted">{subtitle}</p>}
      </div>
      {right && <div className="flex items-center gap-2 shrink-0">{right}</div>}
    </div>
  );
}

/* ---------- Badge ---------- */

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
