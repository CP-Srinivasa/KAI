import { describe, expect, it } from "vitest";

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const css = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "../index.css"), "utf-8");

/**
 * Kontrast-Waechter fuer das Light-Theme (SP-8 Teil 2, Operator-Entscheid 17.09.).
 *
 * Quelle der Messung: docs/ui/kontrast_neon_tokens_20260916.md. Dort lagen im
 * Light-Theme `pos` (1,8:1), `info` (2,3:1) und `fg-subtle` (2,8:1) unter 3:1,
 * `accent`, `neg`, `warn` und `ai` nur auf AA-Large-Niveau. Diese Farben tragen
 * Text (Badges, Hilfstexte, Kennzahlen) — also gilt WCAG AA fuer Fliesstext:
 * >= 4,5:1 gegen JEDEN Light-Hintergrund (bg-0 … bg-3).
 *
 * Der Test liest die echten Tokens aus `:root` in index.css und rechnet nach
 * WCAG 2.1 (relative Luminanz). Wer einen Light-Token wieder aufhellt, bekommt
 * hier rot. Das Dark-Theme (`.dark`) ist bewusst nicht Gegenstand.
 */

const AA_TEXT = 4.5;
const BACKGROUNDS = ["bg-0", "bg-1", "bg-2", "bg-3"] as const;
const TEXT_TOKENS = ["fg", "fg-muted", "fg-subtle", "accent", "pos", "neg", "warn", "info", "ai"] as const;
/** Statusfarben stehen als Badge-Text auf ihrer eigenen Toenung (`bg-pos/10 text-pos`, Primitives.tsx). */
const STATUS_TOKENS = ["accent", "pos", "neg", "warn", "info", "ai"] as const;
const BADGE_TINT_ALPHA = 0.1;

type Rgb = [number, number, number];

function lightTokens(source: string): Record<string, Rgb> {
  const block = /:root\s*\{([^}]*)\}/.exec(source);
  if (!block) throw new Error(":root-Block in index.css nicht gefunden");
  const out: Record<string, Rgb> = {};
  for (const m of block[1].matchAll(/--([a-z0-9-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;/g)) {
    out[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return out;
}

function luminance([r, g, b]: Rgb): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrastRatio(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("Light-Theme-Kontrast (WCAG AA)", () => {
  const tokens = lightTokens(css);

  it("rechnet wie die Messung (Referenz: pos alt #00D787 auf bg-1 = 1,90)", () => {
    expect(contrastRatio([0, 215, 135], [255, 255, 255])).toBeCloseTo(1.9, 2);
  });

  it("findet alle Hintergruende und Text-Tokens im :root-Block", () => {
    for (const name of [...BACKGROUNDS, ...TEXT_TOKENS]) {
      expect(tokens[name], `--${name} fehlt in :root`).toBeDefined();
    }
  });

  for (const fg of STATUS_TOKENS) {
    it(`--${fg} erreicht >= ${AA_TEXT}:1 auf der eigenen Badge-Toenung (bg-${fg}/10)`, () => {
      const failures = BACKGROUNDS.map((bg) => {
        const tint = tokens[bg].map((b, i) => (1 - BADGE_TINT_ALPHA) * b + BADGE_TINT_ALPHA * tokens[fg][i]) as Rgb;
        return { bg, ratio: contrastRatio(tokens[fg], tint) };
      })
        .filter((r) => r.ratio < AA_TEXT)
        .map((r) => `${fg}/10 ueber ${r.bg}: ${r.ratio.toFixed(2)}:1`);
      expect(failures, `--${fg} auf Badge-Toenung unter AA`).toEqual([]);
    });
  }

  for (const fg of TEXT_TOKENS) {
    it(`--${fg} erreicht >= ${AA_TEXT}:1 auf allen Light-Hintergruenden`, () => {
      const failures = BACKGROUNDS.map((bg) => ({ bg, ratio: contrastRatio(tokens[fg], tokens[bg]) }))
        .filter((r) => r.ratio < AA_TEXT)
        .map((r) => `${r.bg}: ${r.ratio.toFixed(2)}:1`);
      expect(failures, `--${fg} unter AA`).toEqual([]);
    });
  }
});
