export type Tone = "pos" | "neg" | "warn" | "info" | "ai" | "neutral";

// Tier-Lift-Schwellen sind bewusst dreistufig (KAI-Honesty):
// - >= +15pp  : Ziel erreicht (pos)
// - > -10pp   : ehrlich unterhalb Ziel, kein Alarm (warn)
// - <= -10pp  : Tier-Inversion / kritisch (neg)
export function tierLiftTone(pp: number | null | undefined): Tone {
  if (pp == null) return "neutral";
  if (pp >= 15) return "pos";
  if (pp > -10) return "warn";
  return "neg";
}

// Tier-Lift Wilson-95%-CI-Overlap-Check (KpiCard- und QualityBar-shared).
// Lieferant des `Lift unsicher`-Badge: wenn die High-Conviction- und
// Standard-Tier-CIs sich überlappen, ist die Lift-Differenz statistisch
// nicht trennbar (V-DB5 D-1).
type TierLiftCiInputs = {
  priority_tier_high_conviction_ci_low_pct: number | null;
  priority_tier_high_conviction_ci_high_pct: number | null;
  priority_tier_standard_ci_low_pct: number | null;
  priority_tier_standard_ci_high_pct: number | null;
};

export function tierLiftCiOverlap(data: TierLiftCiInputs | null | undefined): boolean {
  if (!data) return false;
  const hLo = data.priority_tier_high_conviction_ci_low_pct;
  const hHi = data.priority_tier_high_conviction_ci_high_pct;
  const sLo = data.priority_tier_standard_ci_low_pct;
  const sHi = data.priority_tier_standard_ci_high_pct;
  if (hLo == null || hHi == null || sLo == null || sHi == null) return false;
  return hLo <= sHi && sLo <= hHi;
}

export const TIER_LIFT_UNCERTAIN_TOOLTIP =
  "Wilson 95% Konfidenzintervalle der beiden Hit-Rates überlappen → Lift-Differenz statistisch nicht trennbar (n zu klein).";

// Konsistentes Wert-Format ("+3.2pp", "-1.8pp", "—") für KpiCard und
// QualityBar. Verhindert das in V-DB5 A-1/2/3 dokumentierte Drift-Risiko
// zwischen `.toFixed(1)pp` und `.toFixed(2)pp`.
export function tierLiftValueFormat(pp: number | null | undefined): string {
  if (pp == null) return "—";
  return `${pp >= 0 ? "+" : ""}${pp.toFixed(1)}pp`;
}
