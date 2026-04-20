import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AlertCircle, CheckCircle2, Info, Radio, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDashboardQuality } from "@/lib/useDashboardQuality";

export type ToastTone = "pos" | "neg" | "warn" | "info" | "neutral";

type ToastInput = {
  tone?: ToastTone;
  title: string;
  detail?: string;
  ttlMs?: number;
  icon?: ReactNode;
};

type ToastItem = ToastInput & { id: string };

type Ctx = {
  toast: (t: ToastInput) => string;
  dismiss: (id: string) => void;
};

const ToastCtx = createContext<Ctx | null>(null);

const DEFAULT_TTL = 5_000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setItems((xs) => xs.filter((x) => x.id !== id));
  }, []);

  const toast = useCallback(
    (input: ToastInput) => {
      const id = `t-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const item: ToastItem = { ...input, id };
      setItems((xs) => [...xs.slice(-4), item]);
      const ttl = input.ttlMs ?? DEFAULT_TTL;
      if (ttl > 0) {
        window.setTimeout(() => dismiss(id), ttl);
      }
      return id;
    },
    [dismiss],
  );

  const ctxValue = useMemo<Ctx>(() => ({ toast, dismiss }), [toast, dismiss]);

  return (
    <ToastCtx.Provider value={ctxValue}>
      {children}
      <LiveEventWatcher />
      <ToastViewport items={items} onDismiss={dismiss} />
    </ToastCtx.Provider>
  );
}

export function useToast(): Ctx {
  const c = useContext(ToastCtx);
  if (!c) throw new Error("useToast outside ToastProvider");
  return c;
}

function ToastViewport({ items, onDismiss }: { items: ToastItem[]; onDismiss: (id: string) => void }) {
  if (items.length === 0) return null;
  return (
    <div
      className="fixed z-50 bottom-4 right-4 flex flex-col gap-2 max-w-sm w-[calc(100vw-2rem)] sm:w-auto pointer-events-none"
      role="region"
      aria-label="Benachrichtigungen"
    >
      {items.map((t) => (
        <ToastCard key={t.id} item={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}

const TONE_CLASS: Record<ToastTone, string> = {
  pos: "border-pos/30 bg-pos/10 text-fg",
  neg: "border-neg/30 bg-neg/10 text-fg",
  warn: "border-warn/30 bg-warn/10 text-fg",
  info: "border-info/30 bg-info/10 text-fg",
  neutral: "border-line bg-bg-1 text-fg",
};

const TONE_ICON_COLOR: Record<ToastTone, string> = {
  pos: "text-pos",
  neg: "text-neg",
  warn: "text-warn",
  info: "text-info",
  neutral: "text-fg-muted",
};

function ToastCard({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  const tone = item.tone ?? "neutral";
  const icon = item.icon ?? defaultIcon(tone);
  return (
    <div
      className={cn(
        "pointer-events-auto rounded-md border shadow-raised px-3 py-2 flex items-start gap-2.5 kai-fade",
        TONE_CLASS[tone],
      )}
      role="status"
    >
      <span className={cn("mt-0.5 shrink-0", TONE_ICON_COLOR[tone])} aria-hidden>
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-semibold tracking-tight break-words">{item.title}</div>
        {item.detail && (
          <div className="mt-0.5 text-2xs text-fg-muted break-words font-mono">{item.detail}</div>
        )}
      </div>
      <button
        onClick={onDismiss}
        aria-label="Schließen"
        className="shrink-0 h-5 w-5 grid place-items-center rounded-sm text-fg-subtle hover:bg-bg-3 hover:text-fg"
      >
        <X size={12} />
      </button>
    </div>
  );
}

function defaultIcon(tone: ToastTone): ReactNode {
  switch (tone) {
    case "pos":
      return <CheckCircle2 size={14} />;
    case "neg":
      return <AlertCircle size={14} />;
    case "warn":
      return <AlertCircle size={14} />;
    case "info":
      return <Info size={14} />;
    default:
      return <Info size={14} />;
  }
}

function LiveEventWatcher() {
  const q = useDashboardQuality(30_000);
  const { toast } = useToast();
  const seenRef = useRef<Set<string>>(new Set());
  const initializedRef = useRef(false);

  useEffect(() => {
    if (q.state !== "ready") return;
    const alerts = q.data.recent_alerts ?? [];
    const ids = alerts.map((a) => `${a.doc_id}|${a.dispatched_at}`);

    if (!initializedRef.current) {
      seenRef.current = new Set(ids);
      initializedRef.current = true;
      return;
    }

    alerts.forEach((a) => {
      const key = `${a.doc_id}|${a.dispatched_at}`;
      if (seenRef.current.has(key)) return;
      seenRef.current.add(key);
      const tone: ToastTone =
        a.sentiment === "bullish" ? "pos" : a.sentiment === "bearish" ? "neg" : "info";
      const assets = a.assets?.length ? a.assets.join(", ") : a.doc_id;
      toast({
        tone,
        title: `Neuer Alert · ${assets}`,
        detail: `${a.sentiment || "—"} · prio ${a.priority ?? "—"} · ${a.dispatched_at}`,
        icon: <Radio size={14} />,
        ttlMs: 7_000,
      });
    });
  }, [q, toast]);

  return null;
}
