import { Bot, Shield, Activity, Wrench, Palette, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// 2026-05-08 Operator-Folge: Custom-Icons fuer SENTR / Watchdog / Architect / DALI.
// Files liegen in web/public/agents/<slug>.ico und werden lazy geladen, damit
// die Icons nicht den Initial-Page-Load blockieren. Andere Agenten (Neo,
// SATOSHI, ...) bekommen weiterhin ihre lucide-Fallback-Icons.

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
  sentr: Shield,
  watchdog: Activity,
  architect: Wrench,
  dali: Palette,
};

export function AgentIcon({
  slug,
  size = 16,
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
        className={cn("inline-block shrink-0 rounded-xs object-contain", className)}
        style={{ width: size, height: size }}
      />
    );
  }
  const Fallback = FALLBACK_LUCIDE[key] ?? Bot;
  return <Fallback size={size} className={className} />;
}
