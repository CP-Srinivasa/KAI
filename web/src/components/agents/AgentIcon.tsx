import { Bot, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// 2026-05-10 DALI-A11-v3: Operator-Wunsch — alte PNG bleibt, neue SVG RECHTS
// daneben. PNG groesser. SVGs gruendlich ueberarbeitet (mehr Detail, mehr 80er).
// Klassen-Namen: links = Tradition (PNG), rechts = Persona-Symbol (SVG, Neon-Glow).

const BASE = import.meta.env.BASE_URL;
// 2026-05-13 DALI v2 S2: 6 Agenten-PNGs einheitlich aus
// C:\Users\sasch\Desktop\<Agent>\<Agent>.png auf 384x384 resized
// (159-287 kB pro Bild) und nach web/public/agents/ deployed.
// Satoshi + Neo neu addiert.
const CUSTOM_ICON_PNG: Record<string, string> = {
  sentr: `${BASE}agents/sentr.png`,
  watchdog: `${BASE}agents/watchdog.png`,
  architect: `${BASE}agents/architect.png`,
  dali: `${BASE}agents/dali.png`,
  satoshi: `${BASE}agents/satoshi.png`,
  neo: `${BASE}agents/neo.png`,
};

type IconProps = { size: number };

// DALI v3: Detaillierte Künstler-Palette mit 6 mehrfarbigen Tupfern und vollem Pinsel.
function DaliIcon({ size }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      {/* Palette-Body */}
      <ellipse cx="14" cy="16" rx="11" ry="9" fill="none" stroke="currentColor" strokeWidth="1.6" />
      {/* Daumenloch unten links */}
      <ellipse cx="10.5" cy="20.5" rx="2.2" ry="1.6" fill="rgb(var(--bg-1))" stroke="currentColor" strokeWidth="1.2" />
      {/* 6 Farbtupfer in Tone-Variationen — Operator-Wunsch "verschiedene Farben" */}
      <circle cx="8.5" cy="11" r="1.5" fill="rgb(var(--neg))" />
      <circle cx="13" cy="9" r="1.5" fill="rgb(var(--warn))" />
      <circle cx="17.5" cy="9.5" r="1.5" fill="rgb(var(--pos))" />
      <circle cx="21" cy="13" r="1.5" fill="rgb(var(--info))" />
      <circle cx="22" cy="17.5" r="1.5" fill="rgb(var(--ai))" />
      <circle cx="18" cy="20" r="1.5" fill="rgb(var(--accent))" />
      {/* Pinsel oben rechts — diagonal */}
      <line x1="22" y1="6" x2="29" y2="2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      {/* Pinsel-Spitze (Borste) */}
      <path d="M 27.5 1.5 L 30.5 4.5 L 29.5 5.5 L 28.5 6 L 26.5 4 L 27 3 Z" fill="currentColor" />
      {/* Borsten-Detail */}
      <line x1="28" y1="2.5" x2="29.5" y2="4" stroke="rgb(var(--bg-0))" strokeWidth="0.4" />
    </svg>
  );
}

// SENTR v3: Solides Vorhängeschloss mit Schäkel-Schatten + zentriertem Schlüsselloch.
function SentrIcon({ size }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      {/* Schäkel oben */}
      <path
        d="M9.5 14 V 11 a 6.5 6.5 0 0 1 13 0 V 14"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
      {/* Schäkel-Tiefe (innerer Strich) */}
      <path
        d="M11.5 14 V 11.5 a 4.5 4.5 0 0 1 9 0 V 14"
        fill="none"
        stroke="currentColor"
        strokeWidth="0.8"
        strokeLinecap="round"
        opacity="0.45"
      />
      {/* Schloss-Korpus mit Tiefe */}
      <rect x="5.5" y="14" width="21" height="14.5" rx="2.5" fill="currentColor" opacity="0.15" />
      <rect x="5.5" y="14" width="21" height="14.5" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
      {/* Schlüsselloch zentriert */}
      <circle cx="16" cy="20" r="2" fill="currentColor" />
      <path d="M16 22 V 25.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

// WATCHDOG v3: Klassischer Knochen mit zwei lobed Enden, leicht diagonal.
function WatchdogIcon({ size }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      {/* Knochen — quer, zwei kreisrunde Enden je 2-fach für Knubbel-Look */}
      <g transform="rotate(-20 16 16)">
        {/* Linker Knubbel oben */}
        <circle cx="6" cy="11.5" r="3.5" fill="currentColor" opacity="0.18" />
        <circle cx="6" cy="11.5" r="3.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
        {/* Linker Knubbel unten */}
        <circle cx="6" cy="20.5" r="3.5" fill="currentColor" opacity="0.18" />
        <circle cx="6" cy="20.5" r="3.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
        {/* Rechter Knubbel oben */}
        <circle cx="26" cy="11.5" r="3.5" fill="currentColor" opacity="0.18" />
        <circle cx="26" cy="11.5" r="3.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
        {/* Rechter Knubbel unten */}
        <circle cx="26" cy="20.5" r="3.5" fill="currentColor" opacity="0.18" />
        <circle cx="26" cy="20.5" r="3.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
        {/* Knochen-Schaft */}
        <rect x="6" y="13" width="20" height="6" fill="currentColor" opacity="0.15" />
        <line x1="6" y1="13" x2="26" y2="13" stroke="currentColor" strokeWidth="1.6" />
        <line x1="6" y1="19" x2="26" y2="19" stroke="currentColor" strokeWidth="1.6" />
      </g>
    </svg>
  );
}

// ARCHITECT v3: Zeichendreieck mit Skala + Lineal unten + Bleistift im 45°.
function ArchitectIcon({ size }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      {/* Lineal unten — horizontale Schiene */}
      <rect x="2" y="26" width="28" height="2.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
      {/* Lineal-Skala-Striche */}
      <line x1="6" y1="26" x2="6" y2="28.5" stroke="currentColor" strokeWidth="0.8" />
      <line x1="11" y1="26" x2="11" y2="28.5" stroke="currentColor" strokeWidth="0.8" />
      <line x1="16" y1="26" x2="16" y2="28.5" stroke="currentColor" strokeWidth="0.8" />
      <line x1="21" y1="26" x2="21" y2="28.5" stroke="currentColor" strokeWidth="0.8" />
      <line x1="26" y1="26" x2="26" y2="28.5" stroke="currentColor" strokeWidth="0.8" />

      {/* Zeichendreieck — 45°-rechtwinklig */}
      <path d="M 4 24 L 24 24 L 4 4 Z" fill="currentColor" opacity="0.12" />
      <path d="M 4 24 L 24 24 L 4 4 Z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      {/* Skala am Hypothenuse */}
      <line x1="9" y1="19" x2="11" y2="21" stroke="currentColor" strokeWidth="0.8" />
      <line x1="13" y1="15" x2="15" y2="17" stroke="currentColor" strokeWidth="0.8" />
      <line x1="17" y1="11" x2="19" y2="13" stroke="currentColor" strokeWidth="0.8" />

      {/* Bleistift oben rechts — diagonal */}
      <g transform="rotate(45 25 6)">
        <rect x="22" y="4.5" width="6" height="3" fill="currentColor" opacity="0.85" />
        <path d="M 28 4.5 L 30 6 L 28 7.5 Z" fill="currentColor" />
        <line x1="22" y1="6" x2="22" y2="6.5" stroke="rgb(var(--bg-0))" strokeWidth="0.6" />
      </g>
    </svg>
  );
}

// SATOSHI v1: Bitcoin-B als geometrisches Glyph (klassischer ₿ mit Ober/Unter-Strich),
// neon-glow ueber currentColor.
function SatoshiIcon({ size }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      {/* Korpus-Kreis (Coin-Andeutung) */}
      <circle cx="16" cy="16" r="13" fill="currentColor" opacity="0.12" />
      <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="1.6" />
      {/* B-Stamm */}
      <rect x="11.5" y="8" width="2.5" height="16" fill="currentColor" />
      {/* B-Ober-Wulst */}
      <path
        d="M 14 8 H 19 a 4 4 0 0 1 0 8 H 14 Z"
        fill="currentColor"
        opacity="0.85"
      />
      {/* B-Unter-Wulst */}
      <path
        d="M 14 15 H 20 a 4.5 4.5 0 0 1 0 9 H 14 Z"
        fill="currentColor"
        opacity="0.95"
      />
      {/* Vertikale Strich-Erweiterung oben + unten (das ikonische ₿) */}
      <rect x="14.5" y="5.5" width="1.8" height="3.5" fill="currentColor" />
      <rect x="17" y="5.5" width="1.8" height="3.5" fill="currentColor" />
      <rect x="14.5" y="23" width="1.8" height="3.5" fill="currentColor" />
      <rect x="17" y="23" width="1.8" height="3.5" fill="currentColor" />
    </svg>
  );
}

// NEO v1: 0/1 Matrix-Cascade als drei vertikale Bit-Spalten,
// einzelne Bits semi-glow.
function NeoIcon({ size }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      {/* Aussenrahmen (Console/Matrix-Frame) */}
      <rect x="3" y="3" width="26" height="26" rx="2" fill="currentColor" opacity="0.08" />
      <rect x="3" y="3" width="26" height="26" rx="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      {/* Linke Spalte: 1 0 1 */}
      <text x="8.5" y="11" fontFamily="monospace" fontWeight="700" fontSize="6" fill="currentColor">1</text>
      <text x="8.5" y="18" fontFamily="monospace" fontWeight="700" fontSize="6" fill="currentColor" opacity="0.55">0</text>
      <text x="8.5" y="25" fontFamily="monospace" fontWeight="700" fontSize="6" fill="currentColor">1</text>
      {/* Mittlere Spalte: 0 1 0 */}
      <text x="14.5" y="11" fontFamily="monospace" fontWeight="700" fontSize="6" fill="currentColor" opacity="0.55">0</text>
      <text x="14.5" y="18" fontFamily="monospace" fontWeight="700" fontSize="6" fill="currentColor">1</text>
      <text x="14.5" y="25" fontFamily="monospace" fontWeight="700" fontSize="6" fill="currentColor" opacity="0.55">0</text>
      {/* Rechte Spalte: 1 1 0 */}
      <text x="20.5" y="11" fontFamily="monospace" fontWeight="700" fontSize="6" fill="currentColor">1</text>
      <text x="20.5" y="18" fontFamily="monospace" fontWeight="700" fontSize="6" fill="currentColor">1</text>
      <text x="20.5" y="25" fontFamily="monospace" fontWeight="700" fontSize="6" fill="currentColor" opacity="0.55">0</text>
    </svg>
  );
}

const CUSTOM_ICON_SVG: Record<string, (props: IconProps) => JSX.Element> = {
  sentr: SentrIcon,
  watchdog: WatchdogIcon,
  architect: ArchitectIcon,
  dali: DaliIcon,
  satoshi: SatoshiIcon,
  neo: NeoIcon,
};

const AGENT_TONE: Record<string, string> = {
  sentr: "text-warn",
  watchdog: "text-info",
  architect: "text-ai",
  dali: "text-accent",
  neo: "text-info",
  satoshi: "text-warn",
};

// FALLBACK_LUCIDE wird seit S2 nicht mehr benoetigt - neo + satoshi haben jetzt
// eigene SVG-Glyphen analog zu sentr/watchdog/architect/dali. Mapping bleibt
// leer als Sicherheitsnetz fuer kuenftige Agenten ohne SVG.
const FALLBACK_LUCIDE: Record<string, LucideIcon> = {};

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
  const pngSrc = CUSTOM_ICON_PNG[key];
  const SvgComp = CUSTOM_ICON_SVG[key];
  const FallbackLucide = FALLBACK_LUCIDE[key];

  // Operator 2026-05-10: alte PNG (Tradition) bleibt links, neue SVG (Persona-Glyph)
  // rechts daneben mit Neon-Glow. PNG ist 1x size, SVG ist gleich gross.
  // Glow nur auf SVG-Side via filter:drop-shadow currentColor — PNG hat eigene
  // Farben und braucht keine zusaetzliche Aura.
  const glowStyle = {
    filter: "drop-shadow(0 0 4px currentColor) drop-shadow(0 0 8px currentColor)",
  };

  return (
    <span className={cn("inline-flex items-center gap-2 shrink-0", className)}>
      {pngSrc && (
        <img
          src={pngSrc}
          alt=""
          aria-hidden
          loading="lazy"
          decoding="async"
          width={size}
          height={size}
          className="inline-block shrink-0 rounded-md object-contain ring-1 ring-fg-subtle/15 bg-bg-1/40 p-0.5"
          style={{ width: size, height: size }}
        />
      )}
      {SvgComp ? (
        <span className={cn("inline-flex shrink-0", tone)} style={glowStyle}>
          <SvgComp size={size} />
        </span>
      ) : FallbackLucide ? (
        <span className={cn("inline-flex shrink-0", tone)} style={glowStyle}>
          <FallbackLucide size={size} strokeWidth={2.25} />
        </span>
      ) : (
        <Bot size={size} className={cn("shrink-0", tone)} strokeWidth={2.25} />
      )}
    </span>
  );
}
