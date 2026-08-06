import { describe, it, expect } from "vitest";
import { fmtDiskGb } from "./BlitzInfoPanel";

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
