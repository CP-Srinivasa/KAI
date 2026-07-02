#!/usr/bin/env node
// Data-source doc-gate (DALI-Audit #5).
//
// Enforces the "eine Berechnungsquelle"-Doktrin at the component level: every
// dashboard panel MUST declare where its data comes from in a header marker.
// A reviewer can then check that the declared endpoint matches the canonical
// CLI/digest source — and a panel that silently rolls its own filtering is
// caught because its marker won't match the canonical fn.
//
// This is a presence gate (cheap, deterministic): it does not verify that the
// endpoint is correct, only that provenance is declared. Marker format:
//
//   // @data-source: /dashboard/api/n-overview        (a fetched endpoint)
//   // @data-source: props (/dashboard/api/quality)   (parent-provided)
//   // @data-source: none (presentational)            (no data)

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const panelsDir = join(here, "..", "src", "components", "panels");
const MARKER = /@data-source:\s*(\S.*?)\s*$/m;

const files = readdirSync(panelsDir)
  .filter((f) => f.endsWith(".tsx") && !f.includes(".test."))
  .sort();

const missing = [];
for (const f of files) {
  const txt = readFileSync(join(panelsDir, f), "utf8");
  const m = txt.match(MARKER);
  if (!m || !m[1].trim()) missing.push(f);
}

if (missing.length > 0) {
  console.error(
    `❌ data-source lint: ${missing.length}/${files.length} Panel(s) ohne '@data-source:'-Annotation:`,
  );
  for (const f of missing) console.error(`   - components/panels/${f}`);
  console.error(
    "\nJedes Panel MUSS seine kanonische Datenquelle im Header deklarieren:\n" +
      "   // @data-source: /dashboard/api/...   (gefetchter Endpoint)\n" +
      "   // @data-source: props (<endpoint>)   (vom Parent gereicht)\n" +
      "   // @data-source: none (presentational)",
  );
  process.exit(1);
}

console.log(`✅ data-source lint: alle ${files.length} Panels deklarieren ihre Quelle.`);
