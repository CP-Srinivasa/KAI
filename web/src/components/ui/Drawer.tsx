import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { cn } from "@/lib/utils";

type DrawerProps = {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  width?: string;
  children: ReactNode;
  footer?: ReactNode;
};

export function Drawer({ open, onClose, title, subtitle, width = "560px", children, footer }: DrawerProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  return (
    <div
      className={cn(
        "fixed inset-0 z-40 transition-opacity",
        open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none",
      )}
      aria-hidden={!open}
    >
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-[1px]"
        onClick={onClose}
      />
      <aside
        className={cn(
          "absolute top-0 right-0 h-full bg-bg-1 border-l border-line-subtle shadow-raised",
          "flex flex-col transition-transform duration-200 ease-out",
          open ? "translate-x-0" : "translate-x-full",
        )}
        style={{ width: `min(${width}, 100vw)` }}
        role="dialog"
        aria-modal="true"
      >
        <header className="h-14 px-5 border-b border-line-subtle flex items-center gap-3 shrink-0">
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold tracking-tight text-fg truncate">{title}</h2>
            {subtitle && <p className="text-2xs text-fg-muted truncate">{subtitle}</p>}
          </div>
          <button
            onClick={onClose}
            className="h-8 w-8 grid place-items-center rounded-sm border border-line-subtle bg-bg-2 text-fg-muted hover:text-fg hover:bg-bg-3 transition-colors"
            aria-label="Close"
          >
            <X size={14} />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
        {footer && (
          <footer className="border-t border-line-subtle px-5 py-3 bg-bg-1 shrink-0">{footer}</footer>
        )}
      </aside>
    </div>
  );
}
