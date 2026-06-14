import { describe, expect, it } from "vitest";
import { convertUsd, formatMoney, formatPct, formatPrice, type FxSnapshot } from "./money";

const USD_FX: FxSnapshot = { USD: 1, EUR: 0.9 };

describe("formatMoney — capital amounts", () => {
  it("USD: leading $, en-US grouping, fixed 2 digits", () => {
    expect(formatMoney(1234.5, { currency: "USD", fx: USD_FX })).toBe("$1,234.50");
    expect(formatMoney(0, { currency: "USD", fx: USD_FX })).toBe("$0.00");
    expect(formatMoney(-50, { currency: "USD", fx: USD_FX })).toBe("-$50.00");
  });

  it("EUR: trailing €, de-DE grouping, converted via fx", () => {
    // 1000 USD * 0.9 = 900 EUR
    expect(formatMoney(1000, { currency: "EUR", fx: USD_FX })).toBe("900,00 €");
    expect(formatMoney(-1000, { currency: "EUR", fx: USD_FX })).toBe("-900,00 €");
  });

  it("coerces Decimal strings and returns em-dash for absent values", () => {
    expect(formatMoney("1234.5", { currency: "USD", fx: USD_FX })).toBe("$1,234.50");
    expect(formatMoney(null, { currency: "USD", fx: USD_FX })).toBe("—");
    expect(formatMoney("", { currency: "USD", fx: USD_FX })).toBe("—");
  });

  it("honors a custom digit count", () => {
    expect(formatMoney(5, { currency: "USD", fx: USD_FX, digits: 0 })).toBe("$5");
  });
});

describe("formatPrice — quoted instrument prices (adaptive decimals)", () => {
  it("uses 2 decimals at >=1000, 4 at >=1, 6 below 1", () => {
    expect(formatPrice(64327, { currency: "USD", fx: USD_FX })).toBe("$64,327.00");
    expect(formatPrice(12.5, { currency: "USD", fx: USD_FX })).toBe("$12.5000");
    expect(formatPrice(0.000355 * 1, { currency: "USD", fx: USD_FX })).toBe("$0.000355");
  });

  it("converts to EUR with trailing symbol", () => {
    expect(formatPrice(2000, { currency: "EUR", fx: USD_FX })).toBe("1.800,00 €");
  });
});

describe("formatPct", () => {
  it("USD locale, fixed 1 digit by default", () => {
    expect(formatPct(12.34, { currency: "USD" })).toBe("12.3%");
  });
  it("EUR locale uses comma, optional signed prefix", () => {
    expect(formatPct(12.5, { currency: "EUR", signed: true })).toBe("+12,5%");
    expect(formatPct(-3, { currency: "EUR" })).toBe("-3,0%");
  });
  it("em-dash for absent", () => {
    expect(formatPct(null, { currency: "USD" })).toBe("—");
  });
});

describe("convertUsd", () => {
  it("multiplies by the active currency rate", () => {
    expect(convertUsd(100, "USD", USD_FX)).toBe(100);
    expect(convertUsd(100, "EUR", USD_FX)).toBe(90);
  });
});
