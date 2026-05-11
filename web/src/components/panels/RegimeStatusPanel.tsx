import { Card, CardHeader, Badge, InfoHint } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type {
  DashboardRegime,
  RegimeClass,
  RegimeSnapshot,
  VolClass,
} from "@/lib/api";

// REGIME-R1 (2026-05-09): read-only Markt-Regime-Anzeige fuer BTC + ETH.
// 2026-05-11 DALI Operator-Klarheit:
//   - Deutsche Klartext-Beschreibung pro Regime.
//   - InfoHint-Tooltips fuer ADX / ATR-Z / RV / +DI/-DI / Hysteresis / Vol-Klassen.
//   - Pending-Hysteresis als zwei Neon-Pillen (1/2 visualisiert).

type RegimeTone = "pos" | "neg" | "warn" | "info" | "muted";

const REGIME_LABEL: Record<RegimeClass, string> = {
  trend_up: "Trend rauf",
  trend_down: "Trend runter",
  breakout_up: "Ausbruch rauf",
  breakout_down: "Ausbruch runter",
  chop_quiet: "Seitwaerts ruhig",
  chop_volatile: "Seitwaerts wild",
  unknown: "Unbekannt",
};

const REGIME_PLAIN: Record<RegimeClass, string> = {
  trend_up: "Anhaltender Aufwaertstrend. Kaeufer in Kontrolle.",
  trend_down: "Anhaltender Abwaertstrend. Verkaeufer in Kontrolle.",
  breakout_up: "Aufwaerts-Ausbruch mit erhoehter Schwankung.",
  breakout_down: "Abwaerts-Ausbruch mit erhoehter Schwankung.",
  chop_quiet: "Seitwaerts ohne klare Richtung. Ruhiges Tape.",
  chop_volatile: "Seitwaerts mit hoher Schwankung. Chaotische Range.",
  unknown: "Keine Klassifikation moeglich (Daten-Luecke oder Indikator-Lauf offen).",
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

const VOL_LABEL: Record<VolClass, string> = {
  vol_low: "Schwankung niedrig",
  vol_normal: "Schwankung normal",
  vol_high: "Schwankung hoch",
};

const VOL_HINT =
  "Realisierte Volatilitaet (RV) der letzten Bars in drei Stufen: niedrig (ruhig), normal (typisch), hoch (heiss). Misst die tatsaechliche Bewegung, nicht die implizite Erwartung.";

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
  if (!iso) return "-";
  return iso.substring(0, 16).replace("T", " ");
}

function formatNumber(value: number | undefined | null, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return value.toFixed(digits);
}

function HysteresisPills({ count, tone }: { count: number; tone: RegimeTone }) {
  const dotCls = TONE_DOT_CLASS[tone];
  const glowCls =
    tone === "pos" ? "glow-pos"
    : tone === "neg" ? "glow-neg"
    : tone === "warn" ? "glow-warn"
    : tone === "info" ? "glow-info"
    : "";
  return (
    <span
      className="inline-flex items-center gap-1"
      aria-label={"Hysterese-Fortschritt " + count + " von 2"}
    >
      <span className={cn("h-1.5 w-3 rounded-full", count >= 1 ? cn(dotCls, glowCls) : "bg-fg-subtle/20")} />
      <span className={cn("h-1.5 w-3 rounded-full", count >= 2 ? cn(dotCls, glowCls) : "bg-fg-subtle/20")} />
    </span>
  );
}

function AssetRow({ snapshot }: { snapshot: RegimeSnapshot }) {
  const tone = REGIME_TONE[snapshot.regime];
  const dotClass = TONE_DOT_CLASS[tone];
  const textClass = TONE_TEXT_CLASS[tone];
  const plain = REGIME_PLAIN[snapshot.regime];
  const pendingClass = snapshot.pending_regime;
  const pendingTone: RegimeTone = pendingClass ? REGIME_TONE[pendingClass] : "muted";
  const pendingCount = snapshot.pending_consecutive ?? 0;
  const remaining = Math.max(0, 2 - pendingCount);

  return (
    <div className="rounded-md border border-line-subtle bg-bg-2/40 p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-2xs font-semibold text-fg-subtle uppercase tracking-wider shrink-0">
            {snapshot.asset}
          </span>
          <span
            className={cn("inline-block h-2 w-2 rounded-full shrink-0", dotClass)}
            aria-hidden="true"
          />
          <span className={cn("text-sm font-semibold truncate", textClass)}>
            {REGIME_LABEL[snapshot.regime]}
          </span>
        </div>
        <span className="inline-flex items-center gap-1 text-2xs text-fg-subtle shrink-0">
          {VOL_LABEL[snapshot.vol_class]}
          <InfoHint label="Schwankungsklasse" hint={VOL_HINT} side="left" />
        </span>
      </div>

      <p className="text-xs text-fg-muted leading-relaxed">{plain}</p>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs font-mono text-fg-subtle">
        <span className="inline-flex items-center gap-1">
          ADX {formatNumber(snapshot.adx)}
          <InfoHint
            label="ADX (Trendstaerke)"
            hint="Average Directional Index. Misst wie STARK ein Trend ist (egal ob hoch oder runter). Faustregel: unter 20 = kaum Trend (Seitwaerts), 25-30 = Ausbruch moeglich, 30+ = klarer Trend."
          />
        </span>
        <span className="inline-flex items-center gap-1">
          +DI {formatNumber(snapshot.plus_di)} / -DI {formatNumber(snapshot.minus_di)}
          <InfoHint
            label="+DI / -DI (Trendrichtung)"
            hint="Directional Indicators. +DI staerker = Kaeufer dominieren (rauf). -DI staerker = Verkaeufer dominieren (runter). Zusammen mit ADX zeigt das WER die Bewegung treibt."
          />
        </span>
        {snapshot.atr_zscore != null && (
          <span className="inline-flex items-center gap-1">
            ATR-Z {formatNumber(snapshot.atr_zscore, 2)}
            <InfoHint
              label="ATR-Z (Schwankungs-Anomalie)"
              hint="Z-Score der Average True Range. Wie stark weicht die aktuelle Schwankung vom eigenen Durchschnitt ab? z groesser 1 heisst: deutlich ueber dem Normalen, oft Ausbruchskandidat. z kleiner 0 heisst: ruhiger als sonst."
            />
          </span>
        )}
      </div>

      {pendingClass && (
        <div className="flex items-center gap-2 pt-1 border-t border-line-subtle/60 flex-wrap">
          <HysteresisPills count={pendingCount} tone={pendingTone} />
          <span className="text-2xs text-fg-muted">
            wechselt evtl. zu{" "}
            <span className={cn("font-semibold", TONE_TEXT_CLASS[pendingTone])}>
              {REGIME_LABEL[pendingClass]}
            </span>
            {" "}- wartet auf {remaining} weitere Bestaetigung{remaining === 1 ? "" : "en"}
          </span>
          <InfoHint
            label="Hysterese (Schein-Wechsel-Filter)"
            hint="Damit ein einzelner Ausreisser-Bar das Regime nicht hin- und herflippt, muss eine neue Klasse zwei aufeinanderfolgende Bars halten, bevor sie offiziell uebernommen wird."
            side="left"
          />
        </div>
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
        title={
          <span className="inline-flex items-center gap-1.5">
            Markt-Regime
            <InfoHint
              label="Markt-Regime"
              hint="Stuendliche Einordnung jedes Assets in eine von sechs Markt-Klassen plus drei Schwankungsstufen. So weisst du auf einen Blick, ob der Markt gerade Trend, Ausbruch oder Seitwaerts zeigt. Read-only, beeinflusst noch keine Trades."
            />
          </span>
        }
        subtitle="Wie sich BTC + ETH gerade verhalten, auf Stunden-Basis klassifiziert."
        right={
          <Badge tone={total > 0 ? "info" : "muted"} dot>
            R1 {total} Asset{total === 1 ? "" : "s"}
          </Badge>
        }
      />
      {total === 0 ? (
        <div className="rounded-md border border-line-subtle bg-bg-2 px-3 py-4 text-xs text-fg-muted leading-relaxed">
          <div className="font-medium text-fg mb-1">Noch keine Klassifikation.</div>
          <p>
            Der erste Snapshot erscheint nach dem naechsten stuendlichen Klassifizierer-Lauf
            (5 Minuten nach voller Stunde).
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
        <div className="mt-4 pt-3 border-t border-line-subtle text-2xs text-fg-muted leading-relaxed space-y-1">
          <div>
            Aktualisiert: <span className="font-mono text-fg">{formatTimestamp(data.generated_at)}</span>
          </div>
          <div className="inline-flex items-center gap-1 flex-wrap">
            <span>6 Markt-Klassen + 3 Schwankungsstufen, gegen Schein-Wechsel ueber 2 Bars hinweg gefiltert.</span>
            <InfoHint
              label="Klassen + Hysterese"
              hint="Sechs Markt-Klassen: Trend rauf/runter, Ausbruch rauf/runter, Seitwaerts ruhig/wild. Drei Schwankungsstufen: niedrig/normal/hoch. Hysterese = ein Wechsel zaehlt erst, wenn er sich ueber zwei aufeinanderfolgende Bars haelt."
              side="left"
            />
          </div>
        </div>
      )}
    </Card>
  );
}
