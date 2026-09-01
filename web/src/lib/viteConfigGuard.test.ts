import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import viteConfig from "../../vite.config";
import { BASE_VAR, LOCAL_DEV_BASE, OPT_IN_VAR } from "./resolveApiBase";

/**
 * Integrationstest der ECHTEN Config-Fabrik aus vite.config.ts.
 *
 * `resolveApiBase.test.ts` prueft die reine Entscheidung. Dieser Test prueft die
 * fehlende Haelfte: dass vite.config.ts sie auch wirklich aufruft, mit den
 * richtigen Argumenten, und dass der `server`-Block im Build vollstaendig
 * entfaellt (⇒ kein localhost im Produktionspfad).
 *
 * Warum als Test und nicht als manuelle Probe: `vite`/`rolldown` laufen auf der
 * Entwicklungsmaschine nicht (node 22.11.0 < 22.12 -> "Cannot find native
 * binding"). Ein echter `npm run dev` ist dort nicht startbar. In CI ist er es.
 */

type ConfigFactory = (env: {
  command: "serve" | "build";
  mode: string;
  isPreview?: boolean;
}) => { server?: { proxy?: Record<string, { target?: string }> }; base?: string };

const factory = viteConfig as unknown as ConfigFactory;

const SAVED: Record<string, string | undefined> = {};
const KEYS = [BASE_VAR, OPT_IN_VAR];

beforeEach(() => {
  for (const k of KEYS) {
    SAVED[k] = process.env[k];
    delete process.env[k];
  }
});

afterEach(() => {
  for (const k of KEYS) {
    if (SAVED[k] === undefined) delete process.env[k];
    else process.env[k] = SAVED[k];
  }
  vi.restoreAllMocks();
});

describe("vite.config.ts — H6 Dev-Guard verdrahtet", () => {
  it("die Config-Fabrik ist eine Funktion (defineConfig gibt sie durch)", () => {
    expect(typeof factory).toBe("function");
  });

  it("serve OHNE Base und OHNE Opt-in bricht ab und nennt beide Variablen", () => {
    let msg = "";
    try {
      factory({ command: "serve", mode: "development" });
      throw new Error("KEIN ABBRUCH — der Guard greift nicht");
    } catch (e) {
      msg = (e as Error).message;
    }
    expect(msg).toContain(BASE_VAR);
    expect(msg).toContain(OPT_IN_VAR);
    expect(msg).toContain(LOCAL_DEV_BASE);
  });

  it("serve MIT expliziter Base startet und proxied genau dorthin", () => {
    process.env[BASE_VAR] = "http://192.168.178.23:8000";
    const cfg = factory({ command: "serve", mode: "development" });
    expect(cfg.server).toBeDefined();
    expect(cfg.server?.proxy?.["/operator"]?.target).toBe("http://192.168.178.23:8000");
    expect(cfg.server?.proxy?.["/dashboard/api"]?.target).toBe("http://192.168.178.23:8000");
  });

  it("serve MIT Opt-in startet auf localhost UND gibt das Banner aus", () => {
    process.env[OPT_IN_VAR] = "1";
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const cfg = factory({ command: "serve", mode: "development" });
    expect(cfg.server?.proxy?.["/operator"]?.target).toBe(LOCAL_DEV_BASE);
    const printed = warn.mock.calls.map((c) => String(c[0])).join("\n");
    expect(printed).toContain("LOCAL DEV");
    expect(printed).toContain("NON-AUTHORITATIVE DATA");
  });

  it("build OHNE jede Variable bricht NICHT ab und hat KEINEN server-Block", () => {
    const cfg = factory({ command: "build", mode: "production" });
    expect(cfg.server).toBeUndefined();
    expect(cfg.base).toBe("/dashboard/");
  });

  it("preview OHNE jede Variable bricht NICHT ab", () => {
    const cfg = factory({ command: "serve", mode: "production", isPreview: true });
    expect(cfg.server).toBeUndefined();
  });

  it("im Build taucht LOCAL_DEV_BASE nirgends in der Config auf", () => {
    const cfg = factory({ command: "build", mode: "production" });
    expect(JSON.stringify(cfg)).not.toContain("127.0.0.1");
  });
});
