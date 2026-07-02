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
