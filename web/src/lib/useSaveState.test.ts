import { describe, expect, it } from "vitest";
import { saveReducer, saveStateText, type SaveState } from "./useSaveState";

const idle: SaveState = { kind: "idle" };

describe("saveReducer — Wird gespeichert → Gespeichert / Fehler", () => {
  it("start zeigt sofort 'wird gespeichert', nie 'gespeichert'", () => {
    const s = saveReducer(idle, { type: "start", label: "Kommando check" });
    expect(s.kind).toBe("saving");
    expect(saveStateText(s)).toBe("Kommando check · wird gespeichert …");
  });

  it("'gespeichert' erst nach ok, mit Detail aus der Antwort", () => {
    const saving = saveReducer(idle, { type: "start", label: "Kommando check" });
    const s = saveReducer(saving, { type: "ok", label: "Kommando check", detail: "id 1a2b3c4d" });
    expect(s.kind).toBe("saved");
    expect(saveStateText(s)).toBe("Kommando check · gespeichert · id 1a2b3c4d");
  });

  it("Fehler traegt die Meldung und bleibt unterscheidbar", () => {
    const saving = saveReducer(idle, { type: "start", label: "Kommando check" });
    const s = saveReducer(saving, { type: "fail", label: "Kommando check", message: "HTTP 503" });
    expect(s.kind).toBe("error");
    expect(saveStateText(s)).toBe("Kommando check · fehlgeschlagen: HTTP 503");
  });

  it("reset fuehrt in idle ohne Text", () => {
    const s = saveReducer({ kind: "error", label: "x", message: "y" }, { type: "reset" });
    expect(s).toEqual(idle);
    expect(saveStateText(s)).toBe("");
  });
});
