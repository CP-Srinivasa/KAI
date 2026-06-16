// Gemeinsame Tone→Farb-Abbildung für die Viz-Primitives (WP-0.2). SVG-Elemente
// nutzen fill/stroke="currentColor" + eine dieser Text-Farbklassen, damit die
// Diagramme exakt dieselbe semantische Palette wie Badge/StatusPill verwenden.
import type { Tone } from "@/lib/tone";

export const TONE_TEXT: Record<Tone, string> = {
  pos: "text-pos",
  neg: "text-neg",
  warn: "text-warn",
  info: "text-info",
  ai: "text-ai",
  neutral: "text-fg-muted",
};

export function toneText(tone: Tone): string {
  return TONE_TEXT[tone];
}
