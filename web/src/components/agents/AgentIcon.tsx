import { Bot, Cpu, Key, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// 2026-05-10 DALI-A11-v2: Custom-PNGs raus, inline-SVG-Icons rein.
// Operator: "DALI = Pinsel + Mischbrett, Architect = Skizze, Watchdog = Knochen,
// SENTR = Schloß. Alles in Neon und 80er Style."
//
// Inline-SVGs sind:
// - skalierbar ohne Pixel-Brei
// - tone-fähig (currentColor + drop-shadow für Neon-Glow)
// - klar ikonografisch (Operator erkennt sofort wer für was steht)
//
// Neo (Code-Tiefenanalyse) → Cpu, SATOSHI (Crypto) → Key bleiben Lucide-Fallbacks.

type IconProps = { size: number; className?: string };

// DALI: Künstler-Palette mit zwei Pinseln + Daumenloch.
// Outline-Stil mit currentColor — Neon-Glow via drop-shadow filter.
function DaliIcon({ size, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {/* Palette mit Daumenloch unten */}
      <path d="M16 5c-6.6 0-12 4.5-12 10 0 4 3 7 7 7 1.5 0 2.5-1 2.5-2.2 0-1.4-1-2-1-2.8 0-1 .8-1.7 1.7-1.7H17c5 0 9-3 9-7C26 8 22 5 16 5z" />
      {/* Farbtupfer */}
      <circle cx="9" cy="11" r="1.3" fill="currentColor" />
      <circle cx="14" cy="9" r="1.3" fill="currentColor" />
      <circle cx="20" cy="10" r="1.3" fill="currentColor" />
      <circle cx="22" cy="14" r="1.3" fill="currentColor" />
      {/* Pinsel oben rechts (diagonal) */}
      <path d="M22 4l5 5" />
      <rect x="25" y="3" width="3.5" height="2.5" rx="0.5" transform="rotate(45 26.75 4.25)" fill="currentColor" />
    </svg>
  );
}

// SENTR: Vorhängeschloss mit Schäkel oben.
function SentrIcon({ size, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {/* Schäkel oben */}
      <path d="M10 14V10c0-3.3 2.7-6 6-6s6 2.7 6 6v4" />
      {/* Schloss-Korpus */}
      <rect x="6" y="14" width="20" height="14" rx="2.5" />
      {/* Schlüsselloch */}
      <circle cx="16" cy="20" r="1.6" fill="currentColor" />
      <path d="M16 21.5v3" />
    </svg>
  );
}

// WATCHDOG: Knochen — Hunde-Wachhund-Symbolik.
function WatchdogIcon({ size, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {/* Knochen mit zwei Knubbeln je Ende */}
      <path d="M9.5 8.5c-2 0-3.5 1.5-3.5 3.5 0 1 .4 1.9 1 2.5l-2 2 2 2c-.6.6-1 1.5-1 2.5 0 2 1.5 3.5 3.5 3.5 1.4 0 2.6-.8 3.2-2L19.3 16l5.5-5.5c.6-1.2 1.8-2 3.2-2 2 0 3.5 1.5 3.5 3.5" />
      {/* Spiegel-Variante komplett: Knochen quer */}
      <path d="M22.5 23.5c2 0 3.5-1.5 3.5-3.5 0-1-.4-1.9-1-2.5l2-2-2-2c.6-.6 1-1.5 1-2.5" />
    </svg>
  );
}

// ARCHITECT: Blueprint-Dreieck (Set-Square / Zeichendreieck) auf Linealkante.
function ArchitectIcon({ size, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {/* Zeichendreieck */}
      <path d="M5 27L26 27L5 6Z" />
      {/* Skala-Striche */}
      <path d="M9 23v2M13 19v2M17 15v2M21 11v2" />
      {/* Bleistift oben rechts */}
      <path d="M22 5l5 5l-2 2l-5-5z" fill="currentColor" />
      <path d="M20 7l5 5" />
    </svg>
  );
}

const CUSTOM_ICON: Record<string, (props: IconProps) => JSX.Element> = {
  sentr: SentrIcon,
  watchdog: WatchdogIcon,
  architect: ArchitectIcon,
  dali: DaliIcon,
};

const AGENT_TONE: Record<string, string> = {
  sentr: "text-warn",      // Security = warn (Wachsamkeit)
  watchdog: "text-info",   // Health = info (cyan-pulsing)
  architect: "text-ai",    // Architektur = ai (violet)
  dali: "text-accent",     // Design = accent (magenta/pink)
  neo: "text-info",        // Code = info
  satoshi: "text-warn",    // Crypto = warn (gold-akzent)
};

const FALLBACK_LUCIDE: Record<string, LucideIcon> = {
  neo: Cpu,
  satoshi: Key,
};

export function AgentIcon({
  slug,
  size = 28,
  className,
}: {
  slug: string;
  size?: number;
  className?: string;
}) {
  const key = slug.toLowerCase();
  const tone = AGENT_TONE[key] ?? "text-fg-muted";
  // 2026-05-10: Neon-Glow via drop-shadow filter mit currentColor.
  // Style ist inline weil Tailwind drop-shadow-Klassen die rgb-Var-CurrentColor
  // nicht aufloesen — direktes filter ist robuster.
  const glowStyle = {
    filter:
      "drop-shadow(0 0 4px currentColor) drop-shadow(0 0 8px currentColor)",
  };
  const CustomComp = CUSTOM_ICON[key];
  if (CustomComp) {
    return (
      <span className={cn("inline-flex shrink-0", tone, className)} style={glowStyle}>
        <CustomComp size={size} />
      </span>
    );
  }
  const Fallback = FALLBACK_LUCIDE[key] ?? Bot;
  return (
    <span className={cn("inline-flex shrink-0", tone, className)} style={glowStyle}>
      <Fallback size={size} strokeWidth={2.25} />
    </span>
  );
}
