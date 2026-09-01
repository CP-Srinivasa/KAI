import { describe, expect, it } from "vitest";

import {
  BASE_VAR,
  LOCAL_DEV_BASE,
  OPT_IN_VAR,
  missingBaseMessage,
  resolveApiBase,
} from "./resolveApiBase";

/**
 * Regressionstests zum H6 Dev-Guard.
 *
 * Der wichtigste Test ist C: er beweist, dass die Konfiguration ohne Base und
 * ohne Opt-in WIRKLICH abbricht. Ohne diesen Nachweis waere der Guard eine
 * Behauptung statt einer Zusicherung.
 *
 * Test A ist die Gegenrichtung und mindestens genauso wichtig: der Build darf
 * NIEMALS abbrechen, sonst bricht der CI-Job `web` und damit der Deploy-Weg.
 */
describe("resolveApiBase", () => {
  it("A: build ohne jede Variable bricht NICHT ab (CI-Job `web` bleibt gruen)", () => {
    const r = resolveApiBase("build", {});
    expect(r.kind).toBe("not-needed");
    expect(r.base).toBeUndefined();
  });

  it("A2: preview bricht NICHT ab (bedient ein fertiges Bundle)", () => {
    const r = resolveApiBase("serve", {}, true);
    expect(r.kind).toBe("not-needed");
  });

  it("B: serve mit expliziter Base nutzt genau diese", () => {
    const r = resolveApiBase("serve", { [BASE_VAR]: "http://192.168.178.23:8000" });
    expect(r).toEqual({ kind: "explicit", base: "http://192.168.178.23:8000" });
  });

  it("B2: explizite Base gilt auch im Build", () => {
    const r = resolveApiBase("build", { [BASE_VAR]: "http://example.invalid" });
    expect(r.kind).toBe("explicit");
  });

  it("C: serve OHNE Base und OHNE Opt-in bricht ab — kein stiller Fallback", () => {
    expect(() => resolveApiBase("serve", {})).toThrowError();
  });

  it("C2: die Fehlermeldung nennt beide Variablen und den unterdrueckten Fallback", () => {
    const msg = missingBaseMessage();
    expect(msg).toContain(BASE_VAR);
    expect(msg).toContain(OPT_IN_VAR);
    expect(msg).toContain(LOCAL_DEV_BASE);
  });

  it("C3: leere oder nur-Leerzeichen-Base zaehlt NICHT als gesetzt", () => {
    expect(() => resolveApiBase("serve", { [BASE_VAR]: "   " })).toThrowError();
  });

  it("D: serve mit Opt-in erlaubt localhost — und markiert es als solches", () => {
    const r = resolveApiBase("serve", { [OPT_IN_VAR]: "1" });
    expect(r).toEqual({ kind: "local-dev-optin", base: LOCAL_DEV_BASE });
  });

  it("D2: Opt-in nur bei exakt '1' — 'true'/'0' reichen nicht", () => {
    expect(() => resolveApiBase("serve", { [OPT_IN_VAR]: "true" })).toThrowError();
    expect(() => resolveApiBase("serve", { [OPT_IN_VAR]: "0" })).toThrowError();
  });

  it("E: explizite Base schlaegt das Opt-in (kein ungewolltes localhost)", () => {
    const r = resolveApiBase("serve", {
      [BASE_VAR]: "http://192.168.178.23:8000",
      [OPT_IN_VAR]: "1",
    });
    expect(r.base).toBe("http://192.168.178.23:8000");
  });

  it("F: in KEINEM Ergebnis taucht localhost auf, ohne dass es benannt ist", () => {
    const cases: Array<Record<string, string>> = [
      {},
      { [BASE_VAR]: "http://pi:8000" },
      { [OPT_IN_VAR]: "1" },
    ];
    for (const env of cases) {
      let r;
      try {
        r = resolveApiBase("serve", env);
      } catch {
        continue; // Abbruch ist der gewuenschte Ausgang
      }
      if (r.base === LOCAL_DEV_BASE) {
        expect(r.kind).toBe("local-dev-optin");
      }
    }
  });
});
