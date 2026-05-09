import type { ReactNode } from "react";

// 2026-05-08 Operator-Folge: Synthwave-Divider unter jedem PageHeader.
// Tragt den 80er-Look durchs ganze Dashboard, weil alle Sub-Pages diese
// Komponente nutzen. Im Dark-Mode bekommt der Title zusaetzlich einen
// dezenten Cyan-Halo (text-shadow), im Light-Mode bewusst nicht.
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
    <div className="space-y-2.5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <h1 className="synthwave-title text-xl font-semibold tracking-tight text-fg">
            {title}
          </h1>
          {sub && <p className="text-xs text-fg-muted mt-1 break-words">{sub}</p>}
        </div>
        {right}
      </div>
      <div className="synthwave-divider" aria-hidden="true" />
    </div>
  );
}
