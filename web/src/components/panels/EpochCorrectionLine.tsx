// @data-source: props (/operator/portfolio-snapshot · /dashboard/api/quality)
import { epochCorrectionNote, measurementCoversNow, type EpochCorrection } from "@/lib/epochCorrection";
import { cn } from "@/lib/utils";
import { useCurrency } from "@/state/CurrencyProvider";

/**
 * Der Korrektur-Vermerk einer Buch-Epoche, dort gerendert, wo ihre Zahl steht.
 *
 * Bis 2026-08-27 lag der Vermerk nur in der API. Die Oberflaeche zeigte fuer
 * die betroffene Epoche ``paper_v2_attested`` sogar eine BERUHIGENDE Zeile
 * ("Track-Record gueltig ab …") — ausgerechnet fuer das Buch, dessen gebuchtes
 * Ergebnis sich nach Abzug der gegen erfundene Preise gebuchten Closes
 * umdreht.
 *
 * Die Komponente rechnet nichts aus. Sie zeigt das gemessene Paar und sagt
 * dazu, WANN es gemessen wurde — denn die Epoche sammelt weiter, und eine
 * korrigierte Summe ohne Stand waere wieder nur eine neue blanke Zahl.
 */
export function EpochCorrectionLine({
  correction,
  liveClosedTotal,
  className,
}: {
  correction: EpochCorrection | null | undefined;
  /** Aktuelle Close-Zahl der Epoche; ``null``, wenn die Ansicht sie nicht kennt. */
  liveClosedTotal: number | null | undefined;
  className?: string;
}) {
  const { fmt } = useCurrency();
  const note = epochCorrectionNote(correction);
  if (!note) return null;

  const current = measurementCoversNow(correction, liveClosedTotal);
  const measuredDay = note.measuredAtUtc.slice(0, 10);

  return (
    <div
      className={cn(
        "rounded-sm border border-neg/40 bg-neg/10 px-1.5 py-1 space-y-0.5",
        className,
      )}
      title={note.tooltip}
    >
      <p className="text-2xs font-mono uppercase tracking-wider text-neg">
        ⚠ {note.headline}
      </p>
      <p className="text-2xs text-fg">
        gebucht{" "}
        <span className={cn("font-mono", note.bookedUsd >= 0 ? "text-pos" : "text-neg")}>
          {note.bookedUsd >= 0 ? "+" : ""}
          {fmt(note.bookedUsd)}
        </span>{" "}
        → bereinigt{" "}
        <span className={cn("font-mono", note.correctedUsd >= 0 ? "text-pos" : "text-neg")}>
          {note.correctedUsd >= 0 ? "+" : ""}
          {fmt(note.correctedUsd)}
        </span>{" "}
        <span className="text-fg-subtle">
          ({note.contaminatedCloses} Closes gegen synthetische Preise)
        </span>
      </p>
      <p className="text-2xs text-fg-subtle">
        {current
          ? `Stand ${measuredDay} · ${note.measuredCloses} Closes · ${note.incidentRef}`
          : `Messung vom ${measuredDay} über ${note.measuredCloses} Closes — die Epoche ist seither weitergelaufen, die bereinigte Zahl ist NICHT der aktuelle Stand · ${note.incidentRef}`}
      </p>
    </div>
  );
}
