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

/* ---------- Kpi ----------
   2026-05-10 DALI-K1: zentrale Kpi-Komponente. Bisher waren Kpi-Funktionen
   in 7+ Pages dupliziert (Trades, Portfolio, AIInsightsPage, Dashboard, ...)
   ohne Hero-Variante. Diese zentrale Variante hat:
   - size: sm/md/lg/hero (text-base / text-lg / text-xl / text-3xl)
   - tone: pos/neg/warn/info/ai/muted/neutral
   - sub: optionaler Mono-Subtext unter Wert
   Operator-Wunsch: Hero-Number pro Page für "was sagt mir diese Seite". */

export type KpiTone = "pos" | "neg" | "warn" | "info" | "ai" | "muted" | "neutral";
export type KpiSize = "sm" | "md" | "lg" | "hero";

const KPI_TONE_TEXT: Record<KpiTone, string> = {
  pos: "text-pos",
  neg: "text-neg",
  warn: "text-warn",
  info: "text-info",
  ai: "text-ai",
  muted: "text-fg-muted",
  neutral: "text-fg",
};

const KPI_SIZE_CLS: Record<KpiSize, string> = {
  sm: "text-base",
  md: "text-lg",
  lg: "text-xl",
  hero: "text-3xl",
};

export function Kpi({
  label,
  value,
  sub,
  tone = "neutral",
  size = "md",
  className,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: KpiTone;
  size?: KpiSize;
  className?: string;
}) {
  return (
    <Card padded className={className}>
      <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">{label}</div>
      <div className={cn("mt-1 font-mono font-semibold", KPI_SIZE_CLS[size], KPI_TONE_TEXT[tone])}>
        {value}
      </div>
      {sub && <div className="mt-1 text-2xs text-fg-subtle font-mono">{sub}</div>}
    </Card>
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
  title,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
  dot?: boolean;
  /** Tooltip-Text (HTML title attribute). DALI v2: fuer Klartext-Erklaerung
   *  der raw-Begriffe (Master-Spec G1 — Forensik-Anker bleibt zugaenglich). */
  title?: string;
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
      title={title}
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
  // Synthwave Stufe 2: jeder Dot bekommt einen kleinen Halo in seiner Tone.
  const glow =
    tone === "pos" ? "glow-pos"
    : tone === "neg" ? "glow-neg"
    : tone === "warn" ? "glow-warn"
    : tone === "info" ? "glow-info"
    : tone === "ai" ? "glow-ai"
    : "";
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
      <span className={cn("relative inline-flex rounded-full h-2 w-2", toneDot(tone), glow)} />
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

/* ---------- InfoHint (DALI-P-026, 2026-05-11) ----------
   Inline-Tooltip-Affordance fuer Fachbegriffe (ADX, ATR-Z, Wilson-CI etc.).
   CSS-only via group + focus-within, kein Portal — bleibt im Card-Stacking-
   Context und vererbt das Synthwave-Glow-Pattern (border-info/40 +
   glow-info-leicht). Operator-Wunsch 2026-05-11: deutsche Klar-Definitionen
   auf einen Blick, ohne ein eigenes Drawer/Modal zu oeffnen.

   - trigger: kleines (i)-Glyph (a11y: button mit aria-label, type=button).
   - hint:    deutsche Erklaerung, max ~2-3 Saetze. Wird unter dem Trigger
              eingeblendet, rechts bzw. links je nach side="left|right".
   - inline:  true → triggert als kleines Symbol neben Text; false → block.
*/

export function InfoHint({
  label,
  hint,
  side = "right",
  className,
  triggerClassName,
}: {
  label: string;
  hint: ReactNode;
  side?: "left" | "right";
  className?: string;
  triggerClassName?: string;
}) {
  const sidePos = side === "left" ? "right-0" : "left-0";
  return (
    <span className={cn("relative inline-flex items-center group", className)}>
      <button
        type="button"
        aria-label={`Erklaerung: ${label}`}
        className={cn(
          "inline-flex h-3.5 w-3.5 items-center justify-center rounded-full",
          "border border-info/40 bg-bg-2 text-info text-[9px] font-bold leading-none",
          "transition-colors hover:border-info hover:bg-info/10",
          "focus:outline-none focus-visible:ring-1 focus-visible:ring-info/60",
          triggerClassName,
        )}
      >
        i
      </button>
      <span
        role="tooltip"
        className={cn(
          "pointer-events-none absolute top-full mt-1.5 z-30 w-64 max-w-[80vw]",
          sidePos,
          "rounded-md border border-info/40 bg-bg-1 px-2.5 py-2",
          "text-2xs leading-relaxed text-fg shadow-panel glow-info",
          "opacity-0 translate-y-1 transition-all duration-150",
          "group-hover:opacity-100 group-hover:translate-y-0 group-hover:pointer-events-auto",
          "group-focus-within:opacity-100 group-focus-within:translate-y-0 group-focus-within:pointer-events-auto",
        )}
      >
        <span className="block text-2xs font-semibold uppercase tracking-wider text-info mb-1">
          {label}
        </span>
        <span className="block text-fg-muted">{hint}</span>
      </span>
    </span>
  );
}

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
  // Clamp pct in [0, 100]: bei value<0 wuerde das CSS width:-X% als 0 rendern
  // und der Balken verschwaende komplett (Bug 2026-05-08, Tier-Lift bei -6pp).
  // subZero rendert einen proportionalen roten Balken VON LINKS, dessen Breite
  // |value|/target abbildet — visualisiert die "Tiefe unter 0".
  const rawPct = hasValue ? (value / target) * 100 : 0;
  const pct = hasValue ? Math.max(0, Math.min(100, rawPct)) : 0;
  const subZero = hasValue && rawPct < 0;
  const subZeroPct = subZero ? Math.min(100, Math.abs(rawPct)) : 0;

  const resolved: Exclude<ProgressTone, "auto"> =
    !hasValue || !sufficientSample
      ? "muted"
      : tone !== "auto"
        ? tone
        : subZero
          ? "neg"
          : pct >= 100
            ? "pos"
            : pct >= 50
              ? "warn"
              : "neg";

  const trackHeight = size === "sm" ? "h-1" : "h-1.5";
  // Operator-Folge 2026-05-08: Track auf bg-line — bg-bg-3 war im Light-Mode
  // zu hell, Bars hoben sich nicht ab. bg-line gibt klaren Kontrast in beiden
  // Modi. Muted-Pattern bleibt fuer "inaktiv/keine Daten" als Diagonalstreifen.
  const trackPattern =
    resolved === "muted"
      ? "bg-bg-3 [background-image:repeating-linear-gradient(45deg,transparent,transparent_3px,rgb(var(--line))_3px,rgb(var(--line))_4px)]"
      : "bg-line";
  // Synthwave-Glow nur auf farbigen Bars, nicht muted (sonst lila-grauer Glow).
  const fillGlow =
    resolved === "muted" ? "" : `glow-${resolved}`;

  // V-DB5 Calibration 2026-05-08 (audit B-2):
  // ARIA: aria-valuenow MUSS >= aria-valuemin sein (WCAG). Bei subZero (value<0)
  // setzen wir aria-valuemin=value statt 0; aria-valuetext erklaert den
  // sub-zero-Zustand fuer Screen-Reader explizit.
  const ariaValueMin = subZero && hasValue ? Math.floor(value) : 0;
  const ariaValueText = subZero && hasValue
    ? `${Math.round(value)} (unter Schwelle ${target})`
    : undefined;

  return (
    <div
      className={cn(trackHeight, "w-full rounded-full overflow-hidden relative", trackPattern, className)}
      role="progressbar"
      aria-valuenow={hasValue ? Math.round(value) : 0}
      aria-valuemin={ariaValueMin}
      aria-valuemax={target}
      aria-label={label}
      aria-valuetext={ariaValueText}
    >
      <div
        className={cn("h-full rounded-full transition-all", PROGRESS_FILL[resolved], fillGlow)}
        style={{ width: `${pct}%` }}
      />
      {/* V-DB5 audit B-1: Sub-zero indicator als Diagonal-Stripes (statt solid),
          damit visuell vom 40%-Goal-Pfad trennbar. Stripes = "negativ-Bereich"
          ist DALI-Konvention analog zu muted-Pattern (DALI-F-033). subZero wird
          nur gerendert wenn sufficientSample, sonst muted-State. */}
      {subZero && sufficientSample && (
        <div
          className="absolute inset-y-0 left-0 rounded-full transition-all [background-image:repeating-linear-gradient(45deg,rgb(var(--neg))_0,rgb(var(--neg))_3px,transparent_3px,transparent_6px)]"
          style={{ width: `${subZeroPct}%` }}
          aria-hidden
          title={`${Math.abs(Math.round(value))} unter Schwelle ${target}`}
        />
      )}
    </div>
  );
}
