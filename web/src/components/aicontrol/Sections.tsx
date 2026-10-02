// Bereiche der KI-Kontrollstation (Stufe 1, nur lesend). Bewertet wird im Backend;
// jedes fehlende Datum ist sichtbar "–" bzw. UNBEKANNT mit Grund, nie eine erfundene 0.

import { AlertTriangle, ExternalLink } from "lucide-react";

import { Card, CardHeader } from "@/components/ui/Primitives";
import {
  formatTokens,
  formatUsd,
  runwayLabel,
  type AiControlHistory,
  type AiControlResponse,
} from "@/lib/aiControl";
import { cn } from "@/lib/utils";

import { StateChip } from "./StateChip";

type R = AiControlResponse;

function Kennzahl({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-2xs uppercase tracking-wider text-fg-muted">{label}</div>
      <div className="text-lg text-fg">{children}</div>
    </div>
  );
}

export function HeaderStrip({ s }: { s: R["summary"] }) {
  const quote = s.today_limit_usd ? Math.min(1, s.today_usd / s.today_limit_usd) : null;
  const ende = s.budget_exhausted_at
    ? `leer seit ${s.budget_exhausted_at.slice(11, 16)} UTC`
    : s.budget_end_estimate === "tagesende"
      ? "reicht bis Tagesende"
      : s.budget_end_estimate
        ? `reicht bis ~${s.budget_end_estimate.slice(11, 16)} UTC`
        : "–";
  return (
    <Card className="glow-border-ai">
      <div className="grid grid-cols-2 gap-3 font-mono text-xs md:grid-cols-4">
        <div>
          <Kennzahl label="Heute">
            {formatUsd(s.today_usd)} <span className="text-fg-muted">/ {formatUsd(s.today_limit_usd)}</span>
          </Kennzahl>
          {quote !== null ? (
            <div className="mt-1 h-1.5 w-full rounded-sm bg-bg-3" aria-hidden>
              <div
                className={cn("h-1.5 rounded-sm", quote >= 1 ? "bg-neg" : quote >= 0.8 ? "bg-warn" : "bg-pos")}
                style={{ width: `${quote * 100}%` }}
              />
            </div>
          ) : null}
        </div>
        <Kennzahl label="Monat">
          {formatUsd(s.month_usd)} <span className="text-fg-muted">/ {formatUsd(s.month_limit_usd)}</span>
        </Kennzahl>
        <Kennzahl label="Prognose">{formatUsd(s.projected_month_usd)}</Kennzahl>
        <Kennzahl label="Budget">
          <span className={s.budget_exhausted_at ? "text-warn" : undefined}>{ende}</span>
        </Kennzahl>
      </div>
      <div className="mt-2 font-mono text-2xs text-fg-subtle">
        heute {s.calls_today} Aufrufe · Token {formatTokens(s.tokens_in_today)}/{formatTokens(s.tokens_out_today)}
      </div>
    </Card>
  );
}

export function AttentionList({ items }: { items: R["attention"] }) {
  return (
    <Card>
      <CardHeader title="Handlungsbedarf" />
      {items.length === 0 ? (
        <div className="font-mono text-xs text-pos">● nichts offen</div>
      ) : (
        <ul className="space-y-1.5">
          {items.map((h) => (
            <li key={h.key} className="flex flex-wrap items-start gap-2 font-mono text-xs">
              <AlertTriangle
                size={13}
                className={cn("mt-0.5 shrink-0", h.severity === "crit" ? "text-neg" : "text-warn")}
                aria-hidden
              />
              <span className="text-fg">{h.title}</span>
              <span className="text-fg-muted">{h.detail}</span>
              {h.action.kind === "topup" && h.action.url ? (
                <a
                  href={h.action.url}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-auto inline-flex items-center gap-1 text-info hover:underline"
                >
                  Aufladen <ExternalLink size={11} aria-hidden />
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function Connections({ c, reasons }: { c: R["connections"]; reasons: Record<string, string> }) {
  const lock =
    c.proxy.lock_matches === null ? "Lock ?" : c.proxy.lock_matches ? "Baum = Lock ✓" : "Baum ≠ Lock";
  return (
    <Card>
      <CardHeader title="Verbindungen" />
      <div className="space-y-2 font-mono text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-36 text-fg-muted">LiteLLM-Proxy</span>
          <StateChip state={c.proxy.state} reason={c.proxy.reason ?? reasons["connections.proxy.state"] ?? null} />
          <span className="text-fg-muted">{c.proxy.version ? `v${c.proxy.version}` : "Version ?"}</span>
          <span className={c.proxy.lock_matches === false ? "text-warn" : "text-fg-muted"}>{lock}</span>
        </div>
        {c.aliases.map((a) => (
          <div key={a.alias} className="flex flex-wrap items-center gap-2 pl-4">
            <span className="w-36 text-fg">{a.alias}</span>
            <span className="text-fg-muted">→ {a.upstream_model ?? "kein Modell"}</span>
            <StateChip state={a.state} reason={a.reason} />
            <span className="text-fg-subtle">{a.mode}</span>
          </div>
        ))}
        {c.providers.length > 0 ? (
          <div className="flex flex-wrap gap-3 pt-1">
            {c.providers.map((p) => (
              <span key={p.name} className="inline-flex items-center gap-1.5">
                <span className="text-fg">{p.name}</span>
                <StateChip state={p.state} reason={p.reason} />
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </Card>
  );
}

export function Workloads({ items }: { items: R["workloads"] }) {
  return (
    <Card>
      <CardHeader title="Wer macht was (heute)" />
      <div className="overflow-x-auto">
        <table className="w-full font-mono text-xs">
          <thead className="text-2xs uppercase tracking-wider text-fg-muted">
            <tr>
              <th className="py-1 text-left">Aufgabe</th>
              <th className="text-left">Zustand</th>
              <th className="text-left">Weg · Modell (Dienst)</th>
              <th className="text-right">Aufr.</th>
              <th className="text-right">Token ein/aus</th>
              <th className="text-right">≈ KB</th>
              <th className="text-right">Kosten</th>
              <th className="text-right">Fehler 24 h</th>
            </tr>
          </thead>
          <tbody>
            {items.map((w) => (
              <tr key={w.purpose} className="border-t border-line/50 align-top">
                <td className="py-1.5 text-fg">
                  {w.title}
                  <div className="text-2xs text-fg-subtle">
                    {w.route} · {w.mode}
                    {w.sparfenster && w.sparfenster !== "off" ? ` · Sparfenster ${w.sparfenster}` : ""}
                  </div>
                </td>
                <td>
                  <StateChip state={w.state} reason={w.reason} />
                </td>
                <td className="text-fg-muted">
                  {w.parts.length === 0
                    ? "–"
                    : w.parts.map((p) => (
                        <div key={`${p.service}-${p.transport}-${p.model}`}>
                          {p.transport} · {p.model} <span className="text-fg-subtle">({p.service})</span>
                        </div>
                      ))}
                </td>
                <td className="text-right">{w.calls_today}</td>
                <td className="text-right">
                  {formatTokens(w.tokens_in_today)}/{formatTokens(w.tokens_out_today)}
                </td>
                <td className="text-right">{w.approx_kb_today.toFixed(0)}</td>
                <td className="text-right">
                  {formatUsd(w.cost_today_usd)}
                  {w.unknown_cost_calls_today ? (
                    <div className="text-2xs text-warn">+{w.unknown_cost_calls_today} ohne Preis</div>
                  ) : null}
                </td>
                <td className="text-right">
                  {w.failure_rate_24h === null ? "–" : `${Math.round(w.failure_rate_24h * 100)} %`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export function Accounts({ items, writtenAt }: { items: R["accounts"]; writtenAt: string | null }) {
  return (
    <Card>
      <CardHeader
        title="Konten & Guthaben"
        subtitle={writtenAt ? `Abfrage ${writtenAt.slice(11, 16)} UTC` : "noch keine Abfrage"}
      />
      {items.length === 0 ? (
        <div className="font-mono text-xs text-fg-muted">keine Kontodaten (Timer kai-ai-control noch nicht gelaufen)</div>
      ) : (
        <div className="grid grid-cols-1 gap-2 font-mono text-xs md:grid-cols-3">
          {items.map((k) => (
            <div key={k.provider} className={cn("rounded-sm border border-line p-2", k.stale && "opacity-70")}>
              <div className="flex items-center justify-between">
                <span className="text-fg">{k.provider}</span>
                <a
                  href={k.topup_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-info hover:underline"
                >
                  Konsole <ExternalLink size={11} aria-hidden />
                </a>
              </div>
              <div className="text-lg text-fg">
                {k.status === "kein_api"
                  ? "nur KAI-Messung"
                  : k.status === "kein_schluessel"
                    ? "kein Schlüssel"
                    : formatUsd(k.balance)}
              </div>
              <div className="text-2xs text-fg-muted">
                {k.status === "fehler"
                  ? `Abfrage fehlgeschlagen (${k.error})`
                  : k.balance !== null
                    ? `Reichweite ${runwayLabel(k.runway_days)}`
                    : ""}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export function Protocol({ items }: { items: R["protocol"] }) {
  return (
    <Card>
      <CardHeader title="Protokoll" subtitle="Änderungen der KI-Schalter und Releases" />
      {items.length === 0 ? (
        <div className="font-mono text-xs text-fg-muted">noch keine Änderung erfasst</div>
      ) : (
        <ul className="space-y-1 font-mono text-xs">
          {items.map((p) => (
            <li key={`${p.ts}-${p.key}`}>
              <span className="text-fg-muted">{p.ts.slice(0, 16).replace("T", " ")}Z</span>{" "}
              <span className="text-fg">{p.key}</span> <span className="text-fg-subtle">{p.old ?? "–"}</span> →{" "}
              <span className="text-ai">{p.new ?? "–"}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function History({ h }: { h: AiControlHistory | null }) {
  if (!h || h.days.length === 0) return null;
  const summen = h.days.map((d) => Object.values(d.cost_by_provider).reduce((a, b) => a + b, 0));
  const max = Math.max(0.01, ...summen);
  return (
    <Card>
      <CardHeader title="Verlauf 14 Tage" subtitle="Kosten je Tag · darunter Budgetende (UTC)" />
      <div className="flex h-28 items-end gap-1">
        {h.days.map((d, i) => (
          <div
            key={d.day}
            className="flex h-full flex-1 flex-col items-center justify-end gap-1"
            title={`${d.day}: ${formatUsd(summen[i])} · ${d.calls} Aufrufe`}
          >
            <div className="w-full rounded-sm bg-ai/70" style={{ height: `${(summen[i] / max) * 100}%` }} />
            <div className="font-mono text-[9px] text-fg-subtle">{d.day.slice(8)}</div>
            <div className="font-mono text-[9px] text-warn">{d.budget_exhausted_at?.slice(11, 16) ?? ""}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}
