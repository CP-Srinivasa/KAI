// KAI Status Badge — small uppercase pill with state-color dot.
// Used in: KaiLiveWidget header (full + compact), header-anchor (compact-route).

import { cn } from "@/lib/utils";
import { KAI_STATUS_LABEL } from "../../kai/constants";
import type { KaiState } from "../../kai/types";

type Props = {
  state: KaiState;
  label?: string;
  className?: string;
};

export function KaiStatusBadge({ state, label, className }: Props) {
  const text = label ?? KAI_STATUS_LABEL[state];

  return (
    <span
      role="status"
      aria-label={`KAI status ${text}`}
      className={cn("kai-status-badge", `kai-state-${state}`, className)}
    >
      {text}
    </span>
  );
}
