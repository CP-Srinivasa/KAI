// Explainer-System (WP-0.3 / Konzept §10): didaktische Mikro-Erklärungen statt
// Textblöcke. Zwei dependency-freie Bausteine:
//   - <InfoHint>   : kleines Info-Icon mit Tooltip (schnelle Klartext-Erklärung).
//   - <Explainer>  : einklappbares <details> mit "Was bedeutet das?"/"Warum wichtig?".
// Bewusst ohne Popover-Lib (kein Bundle-Zuwachs); kritische Zustände werden NIE
// hinter dem Explainer versteckt — er ergänzt nur, verbirgt nichts.
import { useId } from "react";
import { Info, HelpCircle } from "lucide-react";
import { cn } from "@/lib/utils";

/** Inline-Info-Icon mit Tooltip. Für die "Was bedeutet dieser Zustand?"-Kurzhilfe. */
export function InfoHint({ text, className }: { text: string; className?: string }) {
  return (
    <span
      role="note"
      aria-label={text}
      title={text}
      className={cn(
        "inline-flex cursor-help items-center text-fg-subtle hover:text-fg-muted",
        className,
      )}
    >
      <Info size={12} aria-hidden />
    </span>
  );
}

/** Einklappbarer Erklärer. `what` = "Was bedeutet das?", `why` = "Warum wichtig?".
 *  Default eingeklappt — verbraucht keinen Prime-Platz, bleibt on-demand erreichbar. */
export function Explainer({
  summary,
  what,
  why,
  defaultOpen = false,
  className,
}: {
  summary: string;
  what?: string;
  why?: string;
  defaultOpen?: boolean;
  className?: string;
}) {
  const id = useId();
  return (
    <details open={defaultOpen} className={cn("group rounded-sm border border-line-subtle bg-bg-2", className)}>
      <summary
        aria-controls={id}
        className="flex cursor-pointer list-none items-center gap-1.5 px-2.5 py-1.5 text-2xs text-fg-muted marker:hidden"
      >
        <HelpCircle size={12} aria-hidden className="text-info" />
        <span className="font-medium">{summary}</span>
        <span className="ml-auto text-fg-subtle transition-transform group-open:rotate-90">›</span>
      </summary>
      <div id={id} className="space-y-1.5 px-2.5 pb-2.5 pt-0.5 text-2xs leading-relaxed text-fg-subtle">
        {what && (
          <p>
            <span className="font-semibold text-fg-muted">Was bedeutet das? </span>
            {what}
          </p>
        )}
        {why && (
          <p>
            <span className="font-semibold text-fg-muted">Warum wichtig? </span>
            {why}
          </p>
        )}
      </div>
    </details>
  );
}
