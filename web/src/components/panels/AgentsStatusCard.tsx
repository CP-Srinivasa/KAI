// @data-source: /operator/agents
import { Bot, ExternalLink } from "lucide-react";
import { Card, CardHeader, Badge, StatusDot } from "@/components/ui/Primitives";
import { AgentIcon } from "@/components/agents/AgentIcon";
import { fetchAgents, type AgentListResponse, type AgentSummary, type AgentStatus, type AgentWiring } from "@/lib/api";
import { formatRelative, formatAbsolute } from "@/lib/time";
import { usePolling } from "@/lib/usePolling";
import { useRouter } from "@/state/Router";
import { cn } from "@/lib/utils";

const POLL_MS = 90_000;

function statusTone(s: AgentStatus): "pos" | "warn" | "neg" {
  if (s === "live") return "pos";
  if (s === "prepared") return "warn";
  return "neg";
}

// 2026-09-15 DALI v2.1: deutsche Klartext-Woerter statt der rohen
// Backend-Schluessel, und Status nicht nur ueber Farbe: jedes Wort traegt ein
// Symbol. "prepared"/"offline" waren ausserdem ungenau — `unavailable` heisst
// "Dropbox-Verzeichnis fehlt", nicht "Prozess offline".
function statusLabel(s: AgentStatus): string {
  if (s === "live") return "aktiv";
  if (s === "prepared") return "bereit";
  return "nicht verfügbar";
}

function statusGlyph(s: AgentStatus): string {
  if (s === "live") return "\u25cf";
  if (s === "prepared") return "\u25cb";
  return "\u2715";
}

// autonom = eigener Worker-Handler, laeuft ohne Claude-Code-Sitzung.
// interaktiv = Claude-Code-only, ein Kommando wartet in der Queue.
function wiringLabel(w: AgentWiring): string {
  return w === "autonomous" ? "autonom" : "interaktiv";
}

function wiringTitle(w: AgentWiring): string {
  return w === "autonomous"
    ? "Autonom: hat einen Handler in app/agents/worker.py und laeuft im Agent-Worker, ohne Claude-Code-Sitzung."
    : "Interaktiv: Claude-Code-only. Ein Kommando landet in der Queue und wartet auf eine Sitzung — es laeuft nicht von selbst.";
}

export function AgentsStatusCard() {
  const state = usePolling<AgentListResponse>(fetchAgents, {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const { navigate } = useRouter();

  const agents = state.state === "ready" ? state.data.agents : [];
  // Der Untertitel trug bisher den Report-Zeitstempel — den vierten auf
  // derselben Seite. Er sagt jetzt stattdessen das, was man hier wissen will.
  const autonomous = agents.filter((a) => a.wiring === "autonomous").length;

  return (
    <Card padded>
      <CardHeader
        title="Agent Roster"
        subtitle={
          state.state === "ready"
            ? `${agents.length} Agenten · ${autonomous} autonom im Worker, ${agents.length - autonomous} interaktiv über Claude Code`
            : undefined
        }
        right={
          <Badge tone="muted" dot>
            <Bot size={10} />
            Kontrolle
          </Badge>
        }
      />
      {state.state === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Agenten …</div>
      )}
      {state.state === "error" && (
        <div className="py-3 text-xs text-neg break-words">
          Roster unerreichbar: {state.error.message}
        </div>
      )}
      {state.state === "ready" && agents.length === 0 && (
        <div className="py-4 text-center text-xs text-fg-subtle">
          Keine Agenten registriert.
        </div>
      )}
      {state.state === "ready" && agents.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
          {agents.map((a) => (
            <AgentTile key={a.slug} agent={a} onClick={() => navigate("agents")} />
          ))}
        </div>
      )}
    </Card>
  );
}

function AgentTile({ agent, onClick }: { agent: AgentSummary; onClick: () => void }) {
  const tone = statusTone(agent.status);
  return (
    <button
      onClick={onClick}
      className={cn(
        "group text-left rounded-sm border border-line-subtle bg-bg-1 hover:bg-bg-2 hover:border-line transition-colors p-2.5 space-y-1.5",
      )}
      title={`${agent.name} · ${agent.role}${agent.last_seen ? ` · zuletzt ${formatAbsolute(agent.last_seen)}` : ""}`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <AgentIcon slug={agent.slug} size={26} />
        {/* pulse entfernt: bei elf Kacheln pulsierten dauerhaft alle aktiven
            gleichzeitig — Bewegung ohne Ereignis. */}
        <StatusDot tone={tone} />
        <span className="font-mono text-xs font-semibold truncate flex-1">{agent.name}</span>
        <ExternalLink
          size={10}
          className="text-fg-subtle opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
        />
      </div>
      <div className="flex items-center justify-between text-2xs font-mono">
        <span
          className={cn(
            "inline-flex items-center gap-1",
            tone === "pos" ? "text-pos" : tone === "warn" ? "text-warn" : "text-fg-subtle",
          )}
        >
          <span aria-hidden>{statusGlyph(agent.status)}</span>
          {statusLabel(agent.status)}
        </span>
        <span className="text-fg-subtle">
          {agent.findings_count > 0 ? `${agent.findings_count} findings` : "—"}
        </span>
      </div>
      <div className="flex items-center justify-between gap-1.5 text-2xs">
        <span className="text-fg-subtle font-mono truncate">
          {agent.last_seen ? formatRelative(agent.last_seen) : "nie gesehen"}
        </span>
        <span
          className={cn(
            "shrink-0 rounded-xs border px-1 py-0.5",
            agent.wiring === "autonomous"
              ? "border-info/30 bg-info/10 text-info"
              : "border-line-subtle bg-bg-2 text-fg-subtle",
          )}
          title={wiringTitle(agent.wiring)}
        >
          {wiringLabel(agent.wiring)}
        </span>
      </div>
    </button>
  );
}
