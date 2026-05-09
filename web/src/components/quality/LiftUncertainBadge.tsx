import { TIER_LIFT_UNCERTAIN_TOOLTIP } from "@/lib/tone";

// Inline-Badge für den Tier-Lift "Lift unsicher"-Marker.
// Ersetzt das frühere `n.s.` (V-DB5 D-1) durch deutschen Klartext mit
// gleichem Tooltip-Text. Wird sowohl in Dashboard.tsx (KPI-Helper) als
// auch in QualityBar.tsx (Hint-Suffix) eingesetzt — geteilte Component
// hält Format und Microcopy konsistent (V-DB5 A-1/2/3).
export function LiftUncertainBadge() {
  return (
    <span
      className="ml-1.5 inline-flex items-center rounded-xs border border-line-subtle bg-bg-2 px-1 py-px font-mono text-[10px] uppercase tracking-wide text-fg-subtle"
      title={TIER_LIFT_UNCERTAIN_TOOLTIP}
    >
      Lift unsicher
    </span>
  );
}
