import { useEffect, useState } from "react";
import { Bot, ExternalLink } from "lucide-react";
import { Card, CardHeader, Badge, StatusDot } from "@/components/ui/Primitives";
import { fetchAgents, type AgentListResponse, type AgentSummary, type AgentStatus } from "@/lib/api";
import { formatRelative, formatAbsolute } from "@/lib/time";
import { useRouter } from "@/state/Router";
import { cn } from "@/lib/utils";

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: AgentListResponse }
  | { kind: "error"; message: string };

const POLL_MS = 90_000;

function statusTone(s: AgentStatus): "pos" | "warn" | "neg" {
  if (s === "live") return "pos";
  if (s === "prepared") return "warn";
  return "neg";
}

function statusLabel(s: AgentStatus): string {
  if (s === "live") return "live";
  if (s === "prepared") return "prepared";
  return "offline";
}

export function AgentsStatusCard() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const { navigate } = useRouter();

  useEffect(() => {
    const ctrl = new AbortController();
    let cancelled = false;

    const load = async () => {
      try {
        const data = await fetchAgents(ctrl.signal);
        if (cancelled) return;
        setState({ kind: "ready", data });
      } catch (e) {
        if (cancelled) return;
        setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
      }
    };

    load();
    const id = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      ctrl.abort();
      window.clearInterval(id);
    };
  }, []);

  const agents = state.kind === "ready" ? state.data.agents : [];

  return (
    <Card padded>
      <CardHeader
        title="Agent Roster"
        subtitle={
          state.kind === "ready"
            ? `${agents.length} Agenten · Stand: ${state.data.generated_at.substring(0, 19).replace("T", " ")}`
            : undefined
        }
        right={
          <Badge tone="muted" dot>
            <Bot size={10} />
            Kontrolle
          </Badge>
        }
      />
      {state.kind === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Agenten …</div>
      )}
      {state.kind === "error" && (
        <div className="py-3 text-xs text-neg break-words">
          Roster unerreichbar: {state.message}
        </div>
      )}
      {state.kind === "ready" && agents.length === 0 && (
        <div className="py-4 text-center text-xs text-fg-subtle">
          Keine Agenten registriert.
        </div>
      )}
      {state.kind === "ready" && agents.length > 0 && (
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
        <StatusDot tone={tone} pulse={agent.status === "live"} />
        <span className="font-mono text-xs font-semibold truncate flex-1">{agent.name}</span>
        <ExternalLink
          size={10}
          className="text-fg-subtle opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
        />
      </div>
      <div className="flex items-center justify-between text-2xs font-mono">
        <span
          className={cn(
            "uppercase tracking-wide",
            tone === "pos" ? "text-pos" : tone === "warn" ? "text-warn" : "text-fg-subtle",
          )}
        >
          {statusLabel(agent.status)}
        </span>
        <span className="text-fg-subtle">
          {agent.findings_count > 0 ? `${agent.findings_count} findings` : "—"}
        </span>
      </div>
      <div className="text-2xs text-fg-subtle font-mono truncate">
        {agent.last_seen ? formatRelative(agent.last_seen) : "nie gesehen"}
      </div>
    </button>
  );
}
