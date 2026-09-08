// Paritaet: jeder Agent der Backend-Registry braucht eine eigene Glyphe.
//
// Vorgeschichte (2026-09-08): app/api/routers/agents.py fuehrte 11 Agenten,
// AgentIcon.tsx kannte 6. kai-finder, einstein, xqu, architecture-red-team und
// data-quality-inspector fielen auf ein farbloses <Bot> zurueck und standen
// sichtbar neben den ausgestalteten Kacheln. Der Fehler war still: FALLBACK_LUCIDE
// ist leer, es gibt keine Warnung, nichts schlaegt fehl — die Kachel sieht nur falsch aus.
//
// Dieser Test liest die Registry als WAHRHEITSQUELLE aus der Python-Datei, statt
// die Liste hier zu wiederholen. Eine wiederholte Liste wuerde beim naechsten
// neuen Agenten genauso still veralten wie das Mapping selbst.

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { AGENT_ICON_SLUGS } from "./AgentIcon";

const HERE = dirname(fileURLToPath(import.meta.url));
const REGISTRY = resolve(HERE, "../../../../app/api/routers/agents.py");

function registrySlugs(): string[] {
  const src = readFileSync(REGISTRY, "utf-8");
  const found = [...src.matchAll(/^\s*slug="([a-z0-9-]+)"/gm)].map((m) => m[1]);
  return [...new Set(found)].sort();
}

describe("AgentIcon-Paritaet gegen die Agenten-Registry", () => {
  it("liest ueberhaupt Slugs aus der Registry (sonst prueft der Test nichts)", () => {
    // Gegenprobe gegen einen stillen Leerlauf: aendert sich die Python-Syntax,
    // faende die Regex nichts und der Paritaetstest waere trivial gruen.
    const slugs = registrySlugs();
    expect(slugs.length).toBeGreaterThanOrEqual(11);
    expect(slugs).toContain("sentr");
    expect(slugs).toContain("data-quality-inspector");
  });

  it("hat fuer jeden Registry-Agenten eine eigene Glyphe", () => {
    const missing = registrySlugs().filter((s) => !AGENT_ICON_SLUGS.includes(s));
    expect(missing).toEqual([]);
  });

  it("fuehrt keine Glyphe fuer einen Agenten, den die Registry nicht kennt", () => {
    const slugs = registrySlugs();
    const orphaned = AGENT_ICON_SLUGS.filter((s) => !slugs.includes(s));
    expect(orphaned).toEqual([]);
  });
});
