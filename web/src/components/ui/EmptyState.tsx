import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function EmptyState({
  icon,
  title,
  hint,
  action,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center py-12 px-6 rounded-md border border-dashed border-line bg-bg-2",
        className,
      )}
    >
      {icon && (
        <div className="h-10 w-10 rounded-md bg-bg-3 text-fg-subtle grid place-items-center mb-3">{icon}</div>
      )}
      <div className="text-sm font-semibold text-fg">{title}</div>
      {hint && <p className="mt-1 text-xs text-fg-muted max-w-sm">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function PreparedBadge({ children }: { children?: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-xs border border-dashed border-line px-1.5 py-0.5 text-2xs font-medium text-fg-subtle bg-bg-2">
      <span className="h-1.5 w-1.5 rounded-full bg-fg-subtle/60" />
      {children ?? "vorbereitet"}
    </span>
  );
}
