import {
  Bot,
  ShieldCheck,
  Activity,
  Compass,
  Palette,
  Cpu,
  Key,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

// 2026-05-08 Operator-Folge: Custom-Icons fuer SENTR / Watchdog / Architect / DALI.
// Files liegen in web/public/agents/<slug>.ico und werden lazy geladen, damit
// die Icons nicht den Initial-Page-Load blockieren. Andere Agenten (Neo,
// SATOSHI, ...) bekommen weiterhin ihre lucide-Fallback-Icons.
//
// 2026-05-10 DALI-A11: Default-size 16→22, neuer rounded-md statt rounded-xs.
// Lucide-Fallbacks aufgefrischt fuer "wer macht was?"-Sofortlesbarkeit:
//   SENTR     → ShieldCheck  (Security & Inspection — verifiziertes Schutzschild)
//   Watchdog  → Activity     (Health-Pulse — Heartbeat-Monitor)
//   Architect → Compass      (Navigation/Strukturentscheidung — kein Wrench)
//   DALI      → Palette      (Design/UI/Visual — unveraendert)
//   Neo       → Cpu          (Code-Tiefenanalyse, Logik, Refactor)
//   SATOSHI   → Key          (Krypto/Custody/Key-Material)

// Vite serviert in production unter /dashboard/. Absolute "/agents/..." Pfade
// laufen sonst gegen den FastAPI-Root (401). BASE_URL = "/dashboard/" in prod,
// "/" im dev — beide enden mit Slash.
const BASE = import.meta.env.BASE_URL;
const CUSTOM_ICON: Record<string, string> = {
  sentr: `${BASE}agents/sentr.png`,
  watchdog: `${BASE}agents/watchdog.png`,
  architect: `${BASE}agents/architect.png`,
  dali: `${BASE}agents/dali.png`,
};

const FALLBACK_LUCIDE: Record<string, LucideIcon> = {
  sentr: ShieldCheck,
  watchdog: Activity,
  architect: Compass,
  dali: Palette,
  neo: Cpu,
  satoshi: Key,
};

export function AgentIcon({
  slug,
  size = 22,
  className,
}: {
  slug: string;
  size?: number;
  className?: string;
}) {
  const key = slug.toLowerCase();
  const customSrc = CUSTOM_ICON[key];
  if (customSrc) {
    return (
      <img
        src={customSrc}
        alt=""
        aria-hidden
        loading="lazy"
        decoding="async"
        width={size}
        height={size}
        className={cn(
          "inline-block shrink-0 rounded-md object-contain",
          // 2026-05-10: subtiler Glow + Border, damit das PNG am Dark-BG
          // nicht ausgewaschen wirkt und visuell mit Lucide-Icons gleichzieht.
          "ring-1 ring-fg-subtle/15 bg-bg-1/40 p-0.5",
          className,
        )}
        style={{ width: size, height: size }}
      />
    );
  }
  const Fallback = FALLBACK_LUCIDE[key] ?? Bot;
  return <Fallback size={size} className={cn("shrink-0", className)} strokeWidth={2.25} />;
}
