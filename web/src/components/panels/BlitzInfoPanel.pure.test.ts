import { describe, it, expect } from "vitest";
import { fmtDiskGb, pendingChannelsView } from "./BlitzInfoPanel";

// Operator-Entscheid 2026-09-30 (D-287): der belegte Force-Close-Altfall bleibt in
// der lnd-Rohzahl, darf aber keine Dauerwarnung mehr auslösen.
describe("pendingChannelsView", () => {
  it("only the reconciled legacy case -> visible, no warning", () => {
    expect(pendingChannelsView(1, 1)).toEqual({ text: "1 pending (davon 1 geklärt)", warn: false });
  });
  it("a new pending channel next to the legacy case still warns", () => {
    expect(pendingChannelsView(2, 1)).toEqual({ text: "2 pending (davon 1 geklärt)", warn: true });
  });
  it("without backend annotation everything pending warns (fail-safe)", () => {
    expect(pendingChannelsView(1, undefined)).toEqual({ text: "1 pending", warn: true });
  });
  it("nothing pending -> no warning; unknown -> n/v", () => {
    expect(pendingChannelsView(0, 0)).toEqual({ text: "0 pending", warn: false });
    expect(pendingChannelsView(null, 0)).toEqual({ text: "n/v pending", warn: false });
  });
  it("a reconciled count above the raw count never hides a warning below zero", () => {
    expect(pendingChannelsView(1, 5).warn).toBe(false);
  });
});

// Audit 2026-08-06: disk_total_gb (GB!) wurde als "1903.8TB" gerendert — die
// alte Korrektur-Regex griff bei Dezimalwerten nie. Einheit jetzt ehrlich.
describe("fmtDiskGb", () => {
  it("renders >=1000 GB as TB with one decimal (the 1903.8 case)", () => {
    expect(fmtDiskGb(1903.8)).toBe("1,9TB");
    expect(fmtDiskGb(1000)).toBe("1TB");
  });
  it("renders <1000 GB as GB", () => {
    expect(fmtDiskGb(46.2)).toBe("46,2GB");
    expect(fmtDiskGb(512)).toBe("512GB");
  });
  it("null/undefined -> null (caller renders n/v)", () => {
    expect(fmtDiskGb(null)).toBeNull();
    expect(fmtDiskGb(undefined)).toBeNull();
  });
});
