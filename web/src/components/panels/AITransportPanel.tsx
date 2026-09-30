// @data-source: props (/health/ai/transport)
//
// KI-Transport (LiteLLM) auf der Seite System — NUR LESEND, keine Schalter.
// Kopf: lebt der Proxy, welche Version ist belegt, stimmt der laufende Baum mit
// dem Lock, aus welchem Release laeuft KAI. Darunter eine Zeile je Katalogroute
// (Modus, Beleg-Status, Circuit, Belege je Transport, fehlende Punkte), im Fuss
// Bericht, Nachfrist und abgekoppelte Schatten.
//
// Zwei Regeln, die hier durchgesetzt werden:
//  1. Das Panel bewertet nichts. Beleg-Status kommt nur aus dem Routenbericht,
//     Modus und Circuit fertig vom Backend.
//  2. Jedes `null` ist "nicht belegt" mit Grund (Tooltip + Screenreader), nie 0.

import type { ReactNode } from "react";
import { FileClock, HelpCircle, ShieldAlert, Unplug, Waypoints } from "lucide-react";

import { Badge, Card, CardHeader, SectionLabel } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import { PanelError, PanelLoading, type PanelErrorInfo } from "@/components/ui/PanelState";
import type { AiTransportEvidence, AiTransportResponse, AiTransportRoute } from "@/lib/api";
import {
  NOT_PROVEN,
  circuitSummary,
  evidenceStatusMeta,
  formatAgeHours,
  formatSeconds,
  formatShare,
  missingLabel,
  modeMeta,
  nullReason,
  routeMeta,
  shortHash,
  transportLabel,
  transportOrder,
} from "@/lib/aiTransport";
import { DATE_LOCALE, formatDayTime, formatRelative } from "@/lib/time";
import type { AsyncState } from "@/lib/useApi";
import { cn } from "@/lib/utils";

const ENDPOINT = "/health/ai/transport";
const TITLE = "KI-Transport (LiteLLM)";
const PROXY_PROBE = "127.0.0.1:4000/health/liveliness";

type Reasons = Record<string, string>;

const USD = new Intl.NumberFormat(DATE_LOCALE, { maximumFractionDigits: 4 });

/** Ein `null` aus dem Vertrag: sichtbar "nicht belegt", Grund im Tooltip und fuer Screenreader. */
function NotProven({ reason }: { reason: string }) {
  return (
    <span title={reason} className="inline-flex items-center gap-1 text-fg-subtle">
      <HelpCircle size={11} className="shrink-0" aria-hidden />
      <span>{NOT_PROVEN}</span>
      <span className="sr-only"> – {reason}</span>
    </span>
  );
}

const VALUE_TONE = { pos: "text-pos", neg: "text-neg", fg: "text-fg" } as const;

function Tile({
  label,
  value,
  sub,
  tone = "fg",
  title,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: keyof typeof VALUE_TONE;
  title?: string;
}) {
  return (
    <div className="p-2.5 bg-bg-2 rounded-sm border border-line-subtle min-w-0" title={title}>
      <div className="text-2xs text-fg-subtle uppercase tracking-wide">{label}</div>
      <div className={cn("text-xs font-semibold mt-1 break-words", VALUE_TONE[tone])}>{value}</div>
      {sub != null && <div className="text-2xs text-fg-subtle mt-0.5 font-mono break-words">{sub}</div>}
    </div>
  );
}

function Alert({
  tone,
  icon,
  role,
  title,
  detail,
}: {
  tone: "neg" | "muted";
  icon: ReactNode;
  role: "alert" | "status";
  title: string;
  detail?: ReactNode;
}) {
  return (
    <div
      role={role}
      className={cn(
        "rounded-sm border p-2.5 flex items-start gap-2",
        tone === "neg" ? "border-neg/30 bg-neg/5 text-neg" : "border-line-subtle bg-bg-2 text-fg-muted",
      )}
    >
      <span className="shrink-0 mt-px">{icon}</span>
      <div className="min-w-0">
        <div className={cn("text-xs", tone === "neg" && "font-semibold")}>{title}</div>
        {detail != null && (
          <div className="text-2xs font-mono text-fg-muted mt-0.5 break-words">{detail}</div>
        )}
      </div>
    </div>
  );
}

function EvidenceLine({
  route,
  name,
  ev,
  reasons,
}: {
  route: string;
  name: string;
  ev: AiTransportEvidence;
  reasons: Reasons;
}) {
  const key = (field: string) => `routes.${route}.${name}.${field}`;
  const why = (field: string) => nullReason(reasons, key(field));
  const label = transportLabel(name);

  if (ev.calls === 0) {
    return (
      <div data-testid={`evidence-${name}`} className="text-xs text-fg-subtle">
        <span className="font-semibold text-fg-muted">{label}</span> · keine Aufrufe
      </div>
    );
  }

  // Forensik-Anker: was die Zeile nicht zeigt, steht vollstaendig im Tooltip.
  const orNull = (value: string | null, field: string) =>
    value ?? `${NOT_PROVEN} (${why(field)})`;
  const forensic = [
    `Transport-Status: ${ev.status}`,
    `Kosten bekannt: ${orNull(ev.cost_sum_known_usd != null ? `${USD.format(ev.cost_sum_known_usd)} USD` : null, "cost_sum_known_usd")}`,
    `Aufrufe ohne Kostenangabe: ${ev.cost_unknown}`,
    `Fallback-Quote: ${orNull(ev.fallback_rate != null ? formatShare(ev.fallback_rate) : null, "fallback_rate")}`,
    `max. Wiederholungen: ${orNull(ev.max_retry_count != null ? String(ev.max_retry_count) : null, "max_retry_count")}`,
    `Release: ${orNull(ev.release_sha ? shortHash(ev.release_sha) : null, "release_sha")}`,
  ].join("\n");

  return (
    <div
      data-testid={`evidence-${name}`}
      title={forensic}
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted min-w-0"
    >
      <span className="font-semibold text-fg">{label}</span>
      <span>
        {ev.calls == null ? <NotProven reason={why("calls")} /> : `${ev.calls} ${ev.calls === 1 ? "Aufruf" : "Aufrufe"}`}
      </span>
      <span className="inline-flex flex-wrap items-center gap-1">
        letzter Erfolg{" "}
        {ev.last_success_at ? (
          <time dateTime={ev.last_success_at} title={formatRelative(ev.last_success_at)}>
            {formatDayTime(ev.last_success_at)}
          </time>
        ) : (
          <NotProven reason={why("last_success_at")} />
        )}
      </span>
      {ev.identity_share != null ? (
        <span>Identität {formatShare(ev.identity_share)}</span>
      ) : (
        <span className="inline-flex items-center gap-1">
          Identität <NotProven reason={why("identity_share")} />
        </span>
      )}
      {ev.failures > 0 && <span className="text-neg">{ev.failures} Fehler</span>}
      {ev.newest_age_hours == null ? (
        <span className="inline-flex items-center gap-1">
          Evidenzalter <NotProven reason={why("newest_age_hours")} />
        </span>
      ) : ev.stale ? (
        <Badge tone="warn" dot title="Jüngster Beleg ist älter als die Frist des Routenberichts.">
          Evidenz veraltet · {formatAgeHours(ev.newest_age_hours)}
        </Badge>
      ) : (
        <span>Evidenz {formatAgeHours(ev.newest_age_hours)} alt</span>
      )}
    </div>
  );
}

function RouteRow({ route, reasons }: { route: AiTransportRoute; reasons: Reasons }) {
  const meta = routeMeta(route.route);
  const mode = modeMeta(route.mode);
  const circuit = circuitSummary(route.circuit);
  const report = route.report;
  const status = report ? evidenceStatusMeta(report.status) : null;
  const transports = report?.transports ?? {};
  // KEINE_EVIDENZ sagt schon alles: leere Transportzeilen waeren nur Rauschen.
  const names =
    report && report.status !== "KEINE_EVIDENZ" ? transportOrder(Object.keys(transports)) : [];
  const missing = report?.missing ?? [];

  return (
    <li
      aria-label={`Route ${meta.label}`}
      className="rounded-sm border border-line-subtle bg-bg-2/40 p-2.5 space-y-1.5 min-w-0"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <div className="min-w-0 mr-auto">
          <span className="text-xs font-semibold text-fg">{meta.label}</span>
          {meta.purpose && <span className="ml-2 text-2xs text-fg-subtle">{meta.purpose}</span>}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={mode.tone} title={`Modus ${route.mode}: ${mode.hint}`}>
            {mode.label}
          </Badge>
          {report && status ? (
            <Badge tone={status.tone} dot title={`${report.status}: ${status.hint}`}>
              {status.label}
            </Badge>
          ) : (
            <Badge tone="muted" title={nullReason(reasons, `routes.${route.route}.report`)}>
              <HelpCircle size={10} aria-hidden />
              Bericht nicht belegt
            </Badge>
          )}
          <Badge tone={circuit.tone} dot={circuit.tone !== "muted"} title={circuit.detail}>
            {circuit.label}
          </Badge>
        </div>
      </div>

      {names.map((name) => (
        <EvidenceLine
          key={name}
          route={route.route}
          name={name}
          ev={transports[name]}
          reasons={reasons}
        />
      ))}

      {missing.length > 0 && (
        <div className="text-xs text-warn break-words" title={missing.join(", ")}>
          Fehlt: {missing.map(missingLabel).join(" · ")}
        </div>
      )}

      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-2xs font-mono text-fg-subtle">
        <span title={`Route ${route.route}`} className="break-words">
          {route.alias}
        </span>
        <span>Timeout {formatSeconds(route.timeout_seconds)}</span>
        <span title="Gesamtfrist außerhalb von „aus“ – mit Wiederholungen und Fallback">
          Gesamtfrist {formatSeconds(route.deadline_seconds)}
        </span>
      </div>
    </li>
  );
}

function errorInfo(error: { kind: string; message: string; status: number }): PanelErrorInfo {
  return {
    kind: error.kind,
    // 404 heisst hier fast immer: Dashboard neuer als der laufende Backend-Release.
    message:
      error.kind === "not_found"
        ? `Das laufende Backend kennt ${ENDPOINT} noch nicht – vermutlich ist sein Release älter als dieses Dashboard.`
        : error.message,
    code: error.status ? `HTTP ${error.status}` : null,
    path: ENDPOINT,
  };
}

export function AITransportPanel({ state }: { state: AsyncState<AiTransportResponse> }) {
  if (state.state === "loading") return <PanelLoading label={`${TITLE} wird geladen …`} />;
  if (state.state === "error") {
    return (
      <PanelError title={`${TITLE} nicht verfügbar`} error={errorInfo(state.error)} onRetry={state.reload} />
    );
  }

  const data = state.data;
  const reasons: Reasons = data.null_reasons ?? {};
  const why = (key: string) => nullReason(reasons, key);
  const t = data.transport;
  const rt = data.runtime;
  const r = data.report;
  const routes = Array.isArray(data.routes) ? data.routes : [];
  const counts = r.status_counts;
  const shadowsFull = rt.detached_shadows >= rt.max_detached_shadows;

  const statusCode =
    t.proxy_status_code != null ? `HTTP ${t.proxy_status_code}` : <NotProven reason={why("transport.proxy_status_code")} />;

  return (
    <Card padded>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <Waypoints size={14} className="text-ai shrink-0" aria-hidden />
            {TITLE}
          </span>
        }
        subtitle="Proxy, Lock-Abgleich und Belege je Route · nur lesend"
        right={
          <LiveDot state="ready" generatedAt={data.generated_at} staleAfterMs={150_000} downAfterMs={300_000} />
        }
      />

      <div className="space-y-4">
        {(t.proxy_alive === false || t.lock_matches === false || !r.available) && (
          <div className="space-y-2">
            {t.proxy_alive === false && (
              <Alert
                tone="neg"
                role="alert"
                icon={<Unplug size={14} aria-hidden />}
                title="Proxy antwortet nicht"
                detail={
                  t.proxy_status_code != null
                    ? `${PROXY_PROBE} · HTTP ${t.proxy_status_code}`
                    : `${PROXY_PROBE} · HTTP-Status ${NOT_PROVEN} – ${why("transport.proxy_status_code")}`
                }
              />
            )}
            {t.lock_matches === false && (
              <Alert
                tone="neg"
                role="alert"
                icon={<ShieldAlert size={14} aria-hidden />}
                title="Laufender Transport entspricht nicht dem Lock – Neubau nötig"
                detail={`Baum ${t.tree_spec_sha256 ? shortHash(t.tree_spec_sha256) : NOT_PROVEN} ≠ Lock ${
                  t.lock_sha256 ? shortHash(t.lock_sha256) : NOT_PROVEN
                }`}
              />
            )}
            {!r.available && (
              <Alert
                tone="muted"
                role="status"
                icon={<FileClock size={14} aria-hidden />}
                title="Noch kein Routenbericht – Timer kai-litellm-route-report läuft stündlich"
                detail={reasons["report.generated_at"] || undefined}
              />
            )}
          </div>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          <Tile
            label="Proxy"
            title={`Lebenszeichen: GET ${PROXY_PROBE}`}
            tone={t.proxy_alive === true ? "pos" : t.proxy_alive === false ? "neg" : "fg"}
            value={
              t.proxy_alive == null ? (
                <NotProven reason={why("transport.proxy_alive")} />
              ) : t.proxy_alive ? (
                "lebt"
              ) : (
                "antwortet nicht"
              )
            }
            sub={statusCode}
          />
          <Tile
            label="Version"
            title={t.tree ? `Baum ${t.tree}` : undefined}
            value={t.version ?? <NotProven reason={why("transport.version")} />}
            sub={t.manifest ? `Manifest ${shortHash(t.manifest)}` : undefined}
          />
          <Tile
            label="Beleg"
            title="Zeitpunkt der letzten TRANSPORT_VERIFIED-Zeile"
            value={
              t.verified_at ? formatDayTime(t.verified_at) : <NotProven reason={why("transport.verified_at")} />
            }
            sub={t.verified_at ? formatRelative(t.verified_at) : undefined}
          />
          <Tile
            label="Lock-Abgleich"
            title={`Baum ${t.tree_spec_sha256 ?? NOT_PROVEN}\nLock ${t.lock_sha256 ?? NOT_PROVEN}`}
            tone={t.lock_matches === true ? "pos" : t.lock_matches === false ? "neg" : "fg"}
            value={
              t.lock_matches == null ? (
                <NotProven reason={why("transport.lock_matches")} />
              ) : t.lock_matches ? (
                "stimmt"
              ) : (
                "weicht ab"
              )
            }
            sub={t.lock_sha256 ? `Lock ${shortHash(t.lock_sha256)}` : undefined}
          />
          <Tile
            label="Release"
            title={rt.runtime_commit ?? undefined}
            value={
              rt.runtime_commit ? (
                <span className="font-mono">{shortHash(rt.runtime_commit)}</span>
              ) : (
                <NotProven reason={why("runtime.runtime_commit")} />
              )
            }
            sub={
              rt.runtime_source === "release"
                ? "aus Release"
                : rt.runtime_source === "checkout"
                  ? "aus Checkout"
                  : <NotProven reason={why("runtime.runtime_source")} />
            }
          />
          <Tile
            label="Inferenz"
            title="KAI_INFERENCE_ENABLED · die globale Decke kann Routen nur herabstufen, nie anheben"
            value={rt.enabled ? "an" : "aus"}
            sub={`Decke: ${modeMeta(rt.mode_ceiling).label}`}
          />
        </div>

        <div className="space-y-2">
          <SectionLabel>Routen</SectionLabel>
          <ul aria-label="Routen" className="space-y-1.5">
            {routes.map((route) => (
              <RouteRow key={route.route} route={route} reasons={reasons} />
            ))}
          </ul>
        </div>

        {/* flex-wrap statt shrink-0: in schmalen Containern rutscht der rechte
            Block als Ganzes unter den linken, statt ueber die Karte zu ragen. */}
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-2xs text-fg-subtle border-t border-line-subtle pt-2">
          <span className="min-w-0 break-words">
            {r.available && r.generated_at ? (
              <>
                {`Bericht vom ${formatDayTime(r.generated_at)}`}
                {r.age_hours != null ? (
                  ` (vor ${formatAgeHours(r.age_hours)})`
                ) : (
                  <>
                    {" (Alter "}
                    <NotProven reason={why("report.age_hours")} />
                    {")"}
                  </>
                )}
                {counts &&
                  ` · ${counts.BELEGT} belegt · ${counts.LUECKENHAFT} lückenhaft · ${counts.KEINE_EVIDENZ} ohne Evidenz`}
              </>
            ) : (
              <span className="inline-flex items-center gap-1">
                Bericht <NotProven reason={why("report.generated_at")} />
              </span>
            )}
          </span>
          <span className="min-w-0">
            Schatten-Nachfrist {formatSeconds(rt.shadow_grace_seconds)} ·{" "}
            <span className={cn(shadowsFull && "text-warn")}>
              abgekoppelte Schatten {rt.detached_shadows}/{rt.max_detached_shadows}
            </span>
          </span>
        </div>
      </div>
    </Card>
  );
}
