import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Rocket, AlertCircle, CheckCircle2, Loader2, ShieldCheck } from "lucide-react";
import { Badge, Button, Card, CardHeader } from "@/components/ui/Primitives";
import { useToast } from "@/components/Toast";
import { cn } from "@/lib/utils";
import { ApiError, postRunOnce, type RunOnceResponse, type TradingLoopStatus } from "@/lib/api";

/* 2026-05-12 DALI-arcade-T5: RunOnceLauncher.
   Spec docs/ui/dali_trades_arcade_spec.md F-007 + Operator-Prompt 17-20.
   Ersetzt den PreparedPanel-Stub durch echten Operator-Flow gegen
   POST /operator/trading-loop/run-once.

   Architektur:
   - Eigene Card am Footer der Trades-Page (nach Cycle-Cards-Liste).
   - Grosser LAUNCH-Button (Neon-Glow, neon-pulse - reduced-motion respektiv).
   - Klick oeffnet Inline-Modal (eigener role=dialog, kein Drawer-Reuse -
     Drawer ist Side-Sheet, hier brauchen wir zentralen Confirm-Dialog).
   - Modal: Mode + optionales Symbol-Feld + Idempotency-Key-Pille + Buttons.
   - Submit -> apiPost mit Idempotency-Key-Header. Toast bei Erfolg/Fehler.
   - 409 -> Idempotency-Conflict, Operator klickt "Neuen Key erzeugen".
   - state via onStateChange hochgereicht, damit Trades.tsx die
     Schutzschalter-Pille (Run-Once) reaktiv spiegeln kann
     (BEREIT / IN AUSFUEHRUNG / COOLDOWN).

   A11y:
   - Modal: role=dialog, aria-modal=true, aria-labelledby/aria-describedby,
     Focus-Trap (Tab cycelt in Modal), Esc schliesst, Hintergrund-Klick
     schliesst nur wenn nicht submitting.
   - LAUNCH-Button: aria-describedby auf Effekt-Liste.
   - prefers-reduced-motion: keine pulse-Animation, motion-reduce:animate-none
     fuer Loader2-Spinner.

   Microcopy: deutsch nach Operator-Prompt 19.
*/

export type LauncherUiState = "idle" | "submitting" | "cooldown";

type Props = {
  status: TradingLoopStatus;
  /** Bestaetigte Cycle-Reload-Funktion aus Trades.tsx (cycles.reload). */
  onCycleStarted: () => void;
  /** State-Lift, damit die Schutzschalter-Pille spiegeln kann. */
  onStateChange?: (state: LauncherUiState) => void;
};

const COOLDOWN_MS = 2500;

// UUID v4 - matcht Backend-Pattern [A-Za-z0-9._:-]{1,128}.
// crypto.randomUUID seit 2021 in modernen Browsern (Chrome 92+, Safari 15.4+, FF 95+).
// Fallback fuer alte Browser - defensiv, ohne Backticks.
function newIdempotencyKey(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
  } catch {
    /* fallthrough */
  }
  const r1 = Math.random().toString(36).slice(2, 10);
  const r2 = Math.random().toString(36).slice(2, 10);
  return "cli-" + Date.now() + "-" + r1 + "-" + r2;
}

const SYMBOL_PRESETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"] as const;

export function RunOnceLauncher({ status, onCycleStarted, onStateChange }: Props) {
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [cooldown, setCooldown] = useState(false);
  const [symbol, setSymbol] = useState(""); // leer = Backend-Default (BTC/USDT)
  const [idempKey, setIdempKey] = useState(() => newIdempotencyKey());
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const cooldownTimer = useRef<number | null>(null);
  const launchBtnRef = useRef<HTMLButtonElement>(null);
  const cancelBtnRef = useRef<HTMLButtonElement>(null);
  const submitBtnRef = useRef<HTMLButtonElement>(null);
  const symbolInputRef = useRef<HTMLInputElement>(null);

  const runOnceAllowed = status.run_once_allowed;
  const runOnceBlockReason = status.run_once_block_reason;
  const mode = status.mode;
  const executionEnabled = status.execution_enabled;

  // State-Lift fuer Schutzschalter-Pille
  useEffect(() => {
    if (!onStateChange) return;
    if (submitting) onStateChange("submitting");
    else if (cooldown) onStateChange("cooldown");
    else onStateChange("idle");
  }, [submitting, cooldown, onStateChange]);

  useEffect(() => {
    return () => {
      if (cooldownTimer.current !== null) {
        window.clearTimeout(cooldownTimer.current);
      }
    };
  }, []);

  const openModal = useCallback(() => {
    if (!runOnceAllowed || submitting || cooldown) return;
    setErrorMsg(null);
    setIdempKey(newIdempotencyKey()); // jedes Modal-Oeffnen frischer Key
    setOpen(true);
  }, [runOnceAllowed, submitting, cooldown]);

  const closeModal = useCallback(() => {
    if (submitting) return; // kein Schliessen waehrend Request
    setOpen(false);
    setErrorMsg(null);
    window.setTimeout(() => launchBtnRef.current?.focus(), 0);
  }, [submitting]);

  // Esc + Focus-Trap im Modal
  useEffect(() => {
    if (!open) return;
    const handler = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeModal();
        return;
      }
      if (e.key === "Tab") {
        const order = [symbolInputRef.current, cancelBtnRef.current, submitBtnRef.current].filter(
          Boolean,
        ) as HTMLElement[];
        if (order.length === 0) return;
        const active = document.activeElement as HTMLElement | null;
        const idx = active ? order.indexOf(active) : -1;
        if (e.shiftKey) {
          if (idx <= 0) {
            e.preventDefault();
            order[order.length - 1].focus();
          }
        } else {
          if (idx === order.length - 1 || idx === -1) {
            e.preventDefault();
            order[0].focus();
          }
        }
      }
    };
    window.addEventListener("keydown", handler);
    document.body.style.overflow = "hidden";
    window.setTimeout(() => (symbolInputRef.current ?? cancelBtnRef.current)?.focus(), 0);
    return () => {
      window.removeEventListener("keydown", handler);
      document.body.style.overflow = "";
    };
  }, [open, closeModal]);

  const startCooldown = useCallback(() => {
    setCooldown(true);
    if (cooldownTimer.current !== null) window.clearTimeout(cooldownTimer.current);
    cooldownTimer.current = window.setTimeout(() => {
      setCooldown(false);
      cooldownTimer.current = null;
    }, COOLDOWN_MS);
  }, []);

  const onSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (submitting) return;
      setSubmitting(true);
      setErrorMsg(null);
      try {
        const res: RunOnceResponse = await postRunOnce({
          idempotency_key: idempKey,
          symbol: symbol.trim() || undefined,
        });
        const cycleSym =
          (typeof res.symbol === "string" && res.symbol) || symbol.trim() || "BTC/USDT";
        const replayed = res.idempotency_replayed === true;
        if (replayed) {
          toast({
            tone: "warn",
            title: "Cycle bereits ausgefuehrt",
            detail:
              "Idempotency-Key wurde wiederverwendet (" +
              cycleSym +
              "). Backend hat die alte Antwort zurueckgespielt.",
            ttlMs: 7000,
          });
        } else {
          const cid = typeof res.cycle_id === "string" ? res.cycle_id : null;
          toast({
            tone: "pos",
            title: "Cycle gestartet (" + cycleSym + ")",
            detail: cid
              ? "Cycle-ID " + cid + " - erscheint gleich in der Cycle-Liste."
              : "Erscheint gleich in der Cycle-Liste.",
            ttlMs: 6000,
          });
        }
        setOpen(false);
        startCooldown();
        onCycleStarted();
        window.setTimeout(() => onCycleStarted(), COOLDOWN_MS + 500);
      } catch (e2) {
        if (e2 instanceof ApiError) {
          if (e2.status === 409) {
            setErrorMsg(
              "Dieser Cycle wurde bereits ausgeloest. Klick \"Neuen Key erzeugen\" und versuche es nochmal.",
            );
          } else if (e2.status === 400) {
            setErrorMsg("Backend lehnt Request ab: " + e2.message);
          } else if (e2.kind === "unauthorized" || e2.kind === "forbidden") {
            setErrorMsg("Zugriff verweigert (" + e2.status + "). Cloudflare-Session pruefen.");
          } else if (e2.kind === "server") {
            setErrorMsg("Backend-Fehler (" + e2.status + "): " + e2.message);
          } else if (e2.kind === "network") {
            setErrorMsg("Backend antwortet nicht. Cycle wurde nicht gestartet. Erneut versuchen.");
          } else {
            setErrorMsg("Unerwarteter Fehler (" + e2.status + "): " + e2.message);
          }
        } else {
          setErrorMsg("Unerwarteter Fehler: " + (e2 as Error).message);
        }
      } finally {
        setSubmitting(false);
      }
    },
    [submitting, idempKey, symbol, toast, startCooldown, onCycleStarted],
  );

  const buttonDisabled = !runOnceAllowed || submitting || cooldown;
  const buttonLabel: ReactNode = submitting ? (
    <>
      <Loader2 size={18} className="animate-spin motion-reduce:animate-none" /> CYCLE LAUNCH ...
    </>
  ) : cooldown ? (
    <>
      <CheckCircle2 size={18} /> COOLDOWN
    </>
  ) : (
    <>
      <Rocket size={18} /> CYCLE LAUNCHEN
    </>
  );

  const effectsDescId = "runonce-effects-list";

  return (
    <>
      <Card padded className="synthwave-pulse-edge relative overflow-hidden">
        <CardHeader
          title="Cycle-Launcher - Manueller Trading-Zyklus"
          subtitle="Sicheres Ausloesen eines einzelnen Test- oder Analyse-Zyklus."
          right={
            <Badge tone={executionEnabled ? "warn" : "info"} dot>
              {executionEnabled ? "Live-Modus aktiv (" + mode + ")" : "Paper Trading aktiv"}
            </Badge>
          }
        />

        <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-5 items-center">
          <div className="min-w-0">
            <p className="text-sm text-fg-muted leading-relaxed">
              Diese Funktion erlaubt das kontrollierte Ausfuehren eines einzelnen Test- oder
              Analyse-Zyklus. Auto-Loop bleibt unberuehrt.
            </p>
            <ul
              id={effectsDescId}
              className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-fg-muted list-none"
            >
              <li className="flex items-start gap-1.5">
                <span className="text-info">-</span> Signaltests
              </li>
              <li className="flex items-start gap-1.5">
                <span className="text-info">-</span> Strategiekontrolle
              </li>
              <li className="flex items-start gap-1.5">
                <span className="text-info">-</span> Debugging
              </li>
              <li className="flex items-start gap-1.5">
                <span className="text-info">-</span> Sichere Simulationen
              </li>
            </ul>

            <div className="mt-4 rounded-md border border-line-subtle bg-bg-2 p-2.5">
              <div className="flex items-start gap-2 text-2xs text-fg-muted leading-relaxed">
                <ShieldCheck size={14} className="text-pos shrink-0 mt-0.5" aria-hidden />
                <div>
                  <span className="text-fg font-semibold">Sicherheits-Schichten:</span>{" "}
                  Doppelte Ausfuehrung wird verhindert (Idempotency-Key) - Bestaetigungs-Modal vor
                  jedem Start -{" "}
                  {executionEnabled
                    ? "Live-Trading-Modus aktiv - Vorsicht."
                    : "Aktuell nur im Paper-Modus."}
                </div>
              </div>
            </div>

            {!runOnceAllowed && (
              <div className="mt-3 rounded-md border border-warn/30 bg-warn/5 p-2.5 text-2xs text-warn flex items-start gap-2">
                <AlertCircle size={14} className="shrink-0 mt-0.5" aria-hidden />
                <div>
                  <span className="font-semibold">Aktuell gesperrt.</span>{" "}
                  {runOnceBlockReason ?? "Backend meldet keinen Grund - Status-Endpoint pruefen."}
                </div>
              </div>
            )}
          </div>

          <div className="flex flex-col items-center md:items-end gap-2">
            <button
              ref={launchBtnRef}
              type="button"
              onClick={openModal}
              disabled={buttonDisabled}
              aria-describedby={effectsDescId}
              className={cn(
                "group inline-flex items-center justify-center gap-2 px-7 py-4 rounded-md",
                "font-mono font-bold text-base uppercase tracking-[0.2em] select-none",
                "transition-all duration-150 border-2",
                buttonDisabled
                  ? "border-fg-subtle/30 bg-bg-2 text-fg-subtle cursor-not-allowed"
                  : cooldown
                    ? "border-pos/50 bg-pos/10 text-pos"
                    : [
                        "border-info bg-bg-1 text-info",
                        "hover:bg-info/10 hover:text-info hover:scale-[1.02] active:scale-[0.98]",
                        "glow-info neon-pulse",
                        "focus:outline-none focus-visible:ring-2 focus-visible:ring-info/60",
                      ].join(" "),
              )}
              style={{ minWidth: "220px" }}
            >
              {buttonLabel}
            </button>
            <div className="text-2xs text-fg-subtle">
              {cooldown
                ? "Letzter Cycle gestartet - bitte kurz warten ..."
                : runOnceAllowed
                  ? "Bestaetigungs-Modal oeffnet sich"
                  : "Bedienung gesperrt - siehe Hinweis links"}
            </div>
          </div>
        </div>
      </Card>

      {open && (
        <ConfirmModal
          mode={mode}
          executionEnabled={executionEnabled}
          symbol={symbol}
          setSymbol={setSymbol}
          idempKey={idempKey}
          regenerateKey={() => setIdempKey(newIdempotencyKey())}
          errorMsg={errorMsg}
          submitting={submitting}
          onCancel={closeModal}
          onSubmit={onSubmit}
          cancelBtnRef={cancelBtnRef}
          submitBtnRef={submitBtnRef}
          symbolInputRef={symbolInputRef}
        />
      )}
    </>
  );
}

// -----------------------------------------------------------------------------
// Confirm-Modal (Inline-Component, kein Drawer-Reuse)
// -----------------------------------------------------------------------------

type ModalProps = {
  mode: string;
  executionEnabled: boolean;
  symbol: string;
  setSymbol: (s: string) => void;
  idempKey: string;
  regenerateKey: () => void;
  errorMsg: string | null;
  submitting: boolean;
  onCancel: () => void;
  onSubmit: (e: FormEvent) => void;
  cancelBtnRef: React.RefObject<HTMLButtonElement>;
  submitBtnRef: React.RefObject<HTMLButtonElement>;
  symbolInputRef: React.RefObject<HTMLInputElement>;
};

function ConfirmModal(p: ModalProps) {
  const titleId = "runonce-modal-title";
  const descId = "runonce-modal-desc";

  const onPresetKey = useCallback(
    (e: KeyboardEvent<HTMLButtonElement>, value: string) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        p.setSymbol(value);
      }
    },
    [p],
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/55 backdrop-blur-[2px]"
        onClick={() => !p.submitting && p.onCancel()}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        className="relative w-full max-w-md rounded-lg border border-line bg-bg-1 shadow-raised synthwave-pulse-edge"
      >
        <form onSubmit={p.onSubmit}>
          <header className="px-5 pt-5 pb-3">
            <h2 id={titleId} className="text-base font-semibold tracking-tight text-fg">
              Trading-Zyklus jetzt starten?
            </h2>
            <p id={descId} className="mt-1 text-xs text-fg-muted leading-relaxed">
              Ein einzelner Cycle wird im{" "}
              <span
                className={cn(
                  "font-mono font-semibold",
                  p.executionEnabled ? "text-warn" : "text-info",
                )}
              >
                {p.mode}-Modus
              </span>{" "}
              ausgefuehrt.{" "}
              {p.executionEnabled
                ? "Echtgeld-Trading ist aktiv - echte Boersenorder moeglich."
                : "Es werden keine echten Boersenorder platziert."}
            </p>
          </header>

          <div className="px-5 py-3 space-y-3 border-t border-line-subtle">
            <div>
              <label className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
                Symbol (optional)
              </label>
              <input
                ref={p.symbolInputRef}
                type="text"
                value={p.symbol}
                onChange={(e) => p.setSymbol(e.target.value)}
                placeholder="leer = Backend-Default (BTC/USDT)"
                disabled={p.submitting}
                className="mt-1 w-full h-9 px-2.5 rounded-sm border border-line bg-bg-2 text-fg text-sm font-mono focus:outline-none focus:border-info disabled:opacity-50"
                aria-label="Trading-Symbol fuer den Cycle"
              />
              <div className="mt-1.5 flex flex-wrap gap-1">
                {SYMBOL_PRESETS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => p.setSymbol(s)}
                    onKeyDown={(e) => onPresetKey(e, s)}
                    disabled={p.submitting}
                    className={cn(
                      "text-2xs font-mono px-2 py-0.5 rounded-xs border transition-colors",
                      p.symbol === s
                        ? "border-info/60 bg-info/10 text-info"
                        : "border-line-subtle bg-bg-2 text-fg-muted hover:bg-bg-3 hover:text-fg",
                      p.submitting && "opacity-50 cursor-not-allowed",
                    )}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
                Idempotency-Key
              </label>
              <div className="mt-1 flex items-center gap-2">
                <code
                  className="flex-1 min-w-0 truncate text-2xs font-mono px-2 py-1 rounded-xs border border-line-subtle bg-bg-2 text-fg-muted"
                  title={p.idempKey}
                >
                  {p.idempKey}
                </code>
                <button
                  type="button"
                  onClick={p.regenerateKey}
                  disabled={p.submitting}
                  className="text-2xs font-mono px-2 py-1 rounded-xs border border-line-subtle bg-bg-2 text-fg-muted hover:bg-bg-3 hover:text-fg disabled:opacity-50"
                  title="Neuen Key erzeugen (clientseitig)"
                >
                  Neuen Key erzeugen
                </button>
              </div>
              <div className="mt-1 text-2xs text-fg-subtle leading-relaxed">
                Verhindert versehentliche Doppel-Ausfuehrung. Wird bei jedem Modal-Oeffnen neu
                generiert.
              </div>
            </div>

            {p.errorMsg && (
              <div className="rounded-md border border-neg/30 bg-neg/5 p-2.5 text-xs text-neg flex items-start gap-2">
                <AlertCircle size={14} className="shrink-0 mt-0.5" aria-hidden />
                <div className="min-w-0 break-words">{p.errorMsg}</div>
              </div>
            )}
          </div>

          <footer className="px-5 py-3 border-t border-line-subtle flex items-center justify-end gap-2">
            <Button
              ref={p.cancelBtnRef}
              type="button"
              variant="outline"
              onClick={p.onCancel}
              disabled={p.submitting}
            >
              Abbrechen
            </Button>
            <button
              ref={p.submitBtnRef}
              type="submit"
              disabled={p.submitting}
              className={cn(
                "inline-flex items-center gap-2 h-8 px-4 rounded-sm font-mono font-semibold text-xs uppercase tracking-wider",
                "border-2 transition-colors",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-info/60",
                p.submitting
                  ? "border-info/30 bg-bg-2 text-fg-subtle cursor-wait"
                  : "border-info bg-info/10 text-info hover:bg-info/20 glow-info",
                "disabled:cursor-wait",
              )}
            >
              {p.submitting ? (
                <>
                  <Loader2 size={14} className="animate-spin motion-reduce:animate-none" />
                  Wird gestartet ...
                </>
              ) : (
                <>
                  <Rocket size={14} />
                  Cycle ausfuehren
                </>
              )}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
