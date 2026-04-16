import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { useT } from "@/i18n/I18nProvider";
import { clamp } from "@/lib/utils";

export function RiskMeter({ value = 34 }: { value?: number }) {
  const { t } = useT();
  const v = clamp(value, 0, 100);
  const angle = -90 + (v / 100) * 180;
  const band = v < 33 ? "pos" : v < 66 ? "warn" : "neg";
  const label = v < 33 ? t("primitives.risk_low") : v < 66 ? t("primitives.risk_moderate") : t("primitives.risk_high");

  return (
    <Card padded>
      <CardHeader
        title={t("primitives.risk_score")}
        subtitle={t("primitives.risk_score_sub")}
        right={<Badge tone={band} dot>{label}</Badge>}
      />
      <div className="relative mx-auto w-full max-w-[240px] aspect-[2/1] mt-1">
        <svg viewBox="0 0 200 110" className="w-full h-full">
          <defs>
            <linearGradient id="risk-grad" x1="0" x2="1" y1="0" y2="0">
              <stop offset="0%" stopColor="rgb(var(--pos))" />
              <stop offset="50%" stopColor="rgb(var(--warn))" />
              <stop offset="100%" stopColor="rgb(var(--neg))" />
            </linearGradient>
          </defs>
          {/* track */}
          <path
            d="M 15 100 A 85 85 0 0 1 185 100"
            fill="none"
            stroke="rgb(var(--bg-3))"
            strokeWidth="14"
            strokeLinecap="round"
          />
          {/* value arc */}
          <path
            d="M 15 100 A 85 85 0 0 1 185 100"
            fill="none"
            stroke="url(#risk-grad)"
            strokeWidth="14"
            strokeLinecap="round"
            strokeDasharray={`${(v / 100) * 267} 267`}
          />
          {/* ticks */}
          {[0, 25, 50, 75, 100].map((t) => {
            const a = ((-90 + (t / 100) * 180) * Math.PI) / 180;
            const r1 = 68;
            const r2 = 74;
            const cx = 100 + Math.cos(a) * r1;
            const cy = 100 + Math.sin(a) * r1;
            const cx2 = 100 + Math.cos(a) * r2;
            const cy2 = 100 + Math.sin(a) * r2;
            return (
              <line
                key={t}
                x1={cx}
                y1={cy}
                x2={cx2}
                y2={cy2}
                stroke="rgb(var(--fg-subtle))"
                strokeWidth="1"
              />
            );
          })}
          {/* needle */}
          <g transform={`rotate(${angle} 100 100)`}>
            <line x1="100" y1="100" x2="100" y2="30" stroke="rgb(var(--fg))" strokeWidth="2" strokeLinecap="round" />
            <circle cx="100" cy="100" r="5" fill="rgb(var(--fg))" />
          </g>
        </svg>
        <div className="absolute inset-x-0 bottom-0 text-center">
          <div className="text-[30px] leading-none font-semibold font-mono text-fg">{v}</div>
          <div className="text-2xs uppercase tracking-[0.1em] text-fg-subtle mt-1">/ 100</div>
        </div>
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2 text-2xs">
        <Metric label={t("primitives.vol_30d")} value="1.84%" />
        <Metric label={t("primitives.exposure")} value="62%" />
        <Metric label={t("primitives.drawdown")} value="−2.1%" />
      </div>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-sm border border-line-subtle bg-bg-2 px-2 py-1.5">
      <div className="text-fg-subtle uppercase tracking-[0.08em] text-[10px]">{label}</div>
      <div className="mt-0.5 font-mono text-xs text-fg">{value}</div>
    </div>
  );
}
