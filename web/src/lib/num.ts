// Numeric coercion at the API boundary.
//
// Python serializes Decimal as a JSON *string* (JSON has no decimal type), so a
// backend field declared `number` in our types can arrive as "1234.56" at
// runtime. TypeScript does not catch this — the type lies. Untreated, `a + b`
// silently becomes string concatenation and `x >= 0` compares lexicographically
// (the class of the historical Premium-scale bugs). Coerce every money/Decimal
// field here, once, where the response crosses into the app.

/** Coerce a backend numeric field to a finite number, or null if absent/unparseable. */
export function toNum(v: number | string | null | undefined): number | null {
  if (v == null) return null;
  if (typeof v === "string") {
    // Number("") and Number("   ") are 0, not NaN — an empty money field must
    // not coerce to a real zero. Treat blank strings as absent.
    if (v.trim() === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return Number.isFinite(v) ? v : null;
}

/** Like {@link toNum} but returns `fallback` (default 0) instead of null — for
 * arithmetic-critical fields the backend contract always provides. */
export function toNumOr(v: number | string | null | undefined, fallback = 0): number {
  return toNum(v) ?? fallback;
}
