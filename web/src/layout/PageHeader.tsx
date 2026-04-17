import type { ReactNode } from "react";

export function PageHeader({
  title,
  sub,
  right,
}: {
  title: string;
  sub?: string;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 flex-wrap">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-fg">{title}</h1>
        {sub && <p className="text-xs text-fg-muted mt-1 break-words">{sub}</p>}
      </div>
      {right}
    </div>
  );
}
