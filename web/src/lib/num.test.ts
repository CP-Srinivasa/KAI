import { describe, expect, it } from "vitest";
import { toNum, toNumOr } from "./num";

describe("toNum — Decimal-as-string boundary coercion", () => {
  it("passes finite numbers through", () => {
    expect(toNum(0)).toBe(0);
    expect(toNum(1234.56)).toBe(1234.56);
    expect(toNum(-5)).toBe(-5);
  });

  it("coerces Decimal strings (the actual backend bug class)", () => {
    expect(toNum("1234.56")).toBe(1234.56);
    expect(toNum("-0.355")).toBe(-0.355);
    expect(toNum("0")).toBe(0);
  });

  it("returns null for absent or unparseable values", () => {
    expect(toNum(null)).toBeNull();
    expect(toNum(undefined)).toBeNull();
    expect(toNum("")).toBeNull();
    expect(toNum("n/a")).toBeNull();
    expect(toNum(NaN)).toBeNull();
    expect(toNum(Infinity)).toBeNull();
  });

  it("does NOT string-concatenate when coerced values are added", () => {
    // The regression we guard against: "0" + "1234.56" === "01234.56".
    const cash = toNum("100");
    const positions = toNum("1234.56");
    expect((cash ?? 0) + (positions ?? 0)).toBe(1334.56);
  });

  it("compares numerically, not lexicographically, after coercion", () => {
    // Lexicographic "-5" >= "0" would be true (string compare); numeric is false.
    expect((toNum("-5") ?? 0) >= 0).toBe(false);
    expect((toNum("5") ?? 0) >= 0).toBe(true);
  });
});

describe("toNumOr — arithmetic-critical fields with fallback", () => {
  it("falls back to 0 (default) for null/unparseable", () => {
    expect(toNumOr(null)).toBe(0);
    expect(toNumOr("nope")).toBe(0);
    expect(toNumOr(undefined)).toBe(0);
  });

  it("honors a custom fallback", () => {
    expect(toNumOr(null, 1)).toBe(1);
  });

  it("still coerces real values", () => {
    expect(toNumOr("42.5")).toBe(42.5);
    expect(toNumOr(7)).toBe(7);
  });
});
