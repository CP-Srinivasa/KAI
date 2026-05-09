import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type {
  DashboardRegime,
  RegimeClass,
  RegimeSnapshot,
  VolClass,
} from "@/lib/api";

// REGIME-R1 (2026-05-09): read-only Markt-Regime-Anzeige für BTC + ETH.
// Hourly classifier-Run via kai-regime-classify.timer. KEIN Trade-Effect
// in R1 — Operator-Beobachtung über 14 Tage, dann R2-Decision.

type RegimeTone = "pos" | "neg" | "warn" | "info" | "muted";

const REGIME_LABEL: Record<RegimeClass, string> = {
  trend_up: "Trend ↑",
  trend_down: "Trend ↓",
  breakout_up: "Breakout ↑",
  breakout_down: "Breakout ↓",
  chop_quiet: "Chop (ruhig)",
  chop_volatile: "Chop (volatil)",
  unknown: "Unbekannt",
};

const REGIME_TONE: Record<RegimeClass, RegimeTone> = {
  trend_up: "pos",
  trend_down: "neg",
  breakout_up: "info",
  breakout_down: "warn",
  chop_quiet: "muted",
  chop_volatile: "warn",
  unknown: "muted",
};

const REGIME_HINT: Record<RegimeClass, string> = {
  trend_up: "ADX ≥ 30, +DI dominant — anhaltender Aufwärtstrend.",
  trend_down: "ADX ≥ 30, -DI dominant — anhaltender Abwärtstrend.",
  breakout_up: "ADX 25-30, +DI dominant, ATR-Z ≥ 1 — aufwärts-Breakout mit Vol-Anomalie.",
  breakout_down: "ADX 25-30, -DI dominant, ATR-Z ≥ 1 — abwärts-Breakout mit Vol-Anomalie.",
  chop_quiet: "ADX < 25, Volatilität niedrig — Seitwärts mit ruhigem Tape.",
  chop_volatile: "ADX < 25, Volatilität erhöht — chaotische Range.",
  unknown: "Keine Klassifikation (Daten-Lücke oder Indicator-NaN).",
};

const VOL_LABEL: Record<VolClass, string> = {
  vol_low: "vol low",
  vol_normal: "vol normal",
  vol_high: "vol high",
};

const TONE_DOT_CLASS: Record<RegimeTone, string> = {
  pos: "bg-pos",
  neg: "bg-neg",
  warn: "bg-warn",
  info: "bg-info",
  muted: "bg-fg-subtle/50",
};

const TONE_TEXT_CLASS: Record<RegimeTone, string> = {
  pos: "text-pos",
  neg: "text-neg",
  warn: "text-warn",
  info: "text-info",
  muted: "text-fg-subtle",
};


function formatTimestamp(iso: string): string {
  if (!iso) return "—";
  return iso.substring(0, 16).replace("T", " ");
}

function formatNumber(value: number | undefined | null, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

function AssetRow({ snapshot }: { snapshot: RegimeSnapshot }) {
  const tone = REGIME_TONE[snapshot.regime];
  const dotClass = TONE_DOT_CLASS[tone];
  const textClass = TONE_TEXT_CLASS[tone];
  const hint = REGIME_HINT[snapshot.regime];
  const pending = snapshot.pending_regime
    ? `pending → ${REGIME_LABEL[snapshot.pending_regime]} (${snapshot.pending_consecutive ?? 0}/2)`
    : null;

  return (
    <div className="grid items-baseline gap-2 text-xs" style={{ gridTemplateColumns: "auto 1fr auto" }}>
      <span className="font-mono text-2xs font-semibold text-fg-subtle uppercase tracking-wide">
        {snapshot.asset}
      </span>
      <div className="flex items-baseline gap-1.5 min-w-0" title={hint}>
        <span
          className={cn("inline-block h-2 w-2 rounded-full shrink-0 self-center", dotClass)}
          aria-hidden="true"
        />
        <span className={cn("font-semibold truncate", textClass)}>
          {REGIME_LABEL[snapshot.regime]}
        </span>
        <span className="text-2xs text-fg-subtle shrink-0">{VOL_LABEL[snapshot.vol_class]}</span>
      </div>
      <span className="font-mono text-2xs text-fg-subtle text-right whitespace-nowrap">
        ADX {formatNumber(snapshot.adx)}
        {" · "}±DI {formatNumber(snapshot.plus_di)}/{formatNumber(snapshot.minus_di)}
        {snapshot.atr_zscore != null && (
          <>
            {" · "}z {formatNumber(snapshot.atr_zscore, 2)}
          </>
        )}
      </span>
      {pending && (
        <span className="col-span-3 text-2xs text-fg-muted italic" title="Hysteresis: Regime-Wechsel wird erst nach 2 konsekutiven Bars in der neuen Klasse committed.">
          {pending}
        </span>
      )}
    </div>
  );
}

export function RegimeStatusPanel({ data }: { data: DashboardRegime | null }) {
  const assets = data ? Object.keys(data.by_asset) : [];
  const total = assets.length;

  return (
    <Card padded>
      <CardHeader
        title="Markt-Regime"
        subtitle="Stündliche Klassifikation (ADX + ATR-Z + RV) — Read-only-Phase, kein Trade-Block."
        right={
          <Badge tone={total > 0 ? "info" : "muted"} dot>
            R1 · {total} Asset{total === 1 ? "" : "s"}
          </Badge>
        }
      />
      {total === 0 ? (
        <div className="rounded-md border border-line-subtle bg-bg-2 px-3 py-4 text-xs text-fg-muted leading-relaxed">
          <div className="font-medium text-fg mb-1">Noch keine Klassifikation.</div>
          <p>
            Der erste Snapshot erscheint nach dem nächsten <code className="font-mono text-2xs">kai-regime-classify.timer</code>-Lauf
            (stündlich · 5 Minuten nach voller Stunde).
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {assets.map((asset) => {
            const snap = data!.by_asset[asset];
            return <AssetRow key={asset} snapshot={snap} />;
          })}
        </div>
      )}
      {data?.generated_at && (
        <div className="mt-4 pt-3 border-t border-line-subtle text-2xs text-fg-muted leading-relaxed">
          Stand: <span className="font-mono">{formatTimestamp(data.generated_at)}</span>
          <span className="ml-2">
            · 6 Klassen + 3 Volatility-Klassen · 2-Bar-Hysteresis gegen Flickern.
          </span>
        </div>
      )}
    </Card>
  );
}
