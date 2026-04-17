import { useEffect, useRef, useState } from "react";
import {
  Bot,
  Shield,
  Activity,
  Wrench,
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  Send,
  MessageSquare,
  Terminal,
  FileText,
  Smartphone,
  Monitor,
  Cpu,
} from "lucide-react";
import { PageHeader } from "@/layout/PageHeader";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import {
  fetchAgents,
  fetchAgentDetail,
  fetchAgentMessages,
  postAgentCommand,
  postAgentMessage,
  type AgentEvent,
  type AgentEventSource,
  type AgentStatus,
  type AgentSummary,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type AgentTab = "command" | "chat" | "details";

const STATUS_TONE: Record<AgentStatus, "pos" | "warn" | "muted"> = {
  live: "pos",
  prepared: "warn",
  unavailable: "muted",
};

const STATUS_LABEL: Record<AgentStatus, string> = {
  live: "Live",
  prepared: "Vorbereitet",
  unavailable: "Nicht verfügbar",
};

const STATUS_HINT: Record<AgentStatus, string> = {
  live: "Aktivität in den letzten 24h",
  prepared: "Verzeichnis vorhanden, keine Aktivität in 24h",
  unavailable: "Dropbox-Verzeichnis fehlt — wird beim ersten Kommando angelegt",
};

const ICON_BY_SLUG: Record<string, JSX.Element> = {
  sentr: <Shield size={16} />,
  watchdog: <Activity size={16} />,
  architect: <Wrench size={16} />,
};

export function AgentsPage() {
  const list = useApi(fetchAgents, 30_000);
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="p-4 sm:p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title="Agenten"
        sub="SENTR · Watchdog · Architect — alle ausschließlich von Claude Code ausgeführt"
      />

      <Card padded>
        <div className="flex items-start gap-3 text-xs text-fg-muted leading-relaxed">
          <Bot size={14} className="mt-0.5 text-fg-subtle shrink-0" aria-hidden />
          <div className="min-w-0 space-y-1.5">
            <p>
              Status wird ehrlich aus <code className="font-mono text-2xs">artifacts/agents/&lt;slug&gt;/</code>{" "}
              abgeleitet — keine gefakten Heartbeats. Kommandos werden in eine Queue geschrieben
              und vom Agentenprozess out-of-band konsumiert.
            </p>
            <p className="text-2xs text-fg-subtle">
              Permissions heute: <strong>read + report</strong>. Schreibende Aktionen laufen
              ausschließlich über <code className="font-mono">guarded_write</code> mit Audit-Trail.
            </p>
          </div>
        </div>
      </Card>

      {list.state === "loading" && (
        <Card padded>
          <p className="text-xs text-fg-muted">Lade Agenten-Inventar…</p>
        </Card>
      )}

      {list.state === "error" && (
        <Card padded>
          <div className="flex items-start gap-3">
            <AlertTriangle size={16} className="text-warn mt-0.5 shrink-0" />
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-fg">Fehler beim Laden</h3>
              <p className="mt-1 text-xs text-fg-muted break-words">
                {list.error.kind} ({list.error.status}): {list.error.message}
              </p>
              <p className="mt-2 text-2xs text-fg-subtle font-mono break-all">GET /operator/agents</p>
              <button
                onClick={list.reload}
                className="mt-3 inline-flex items-center gap-1.5 h-7 px-2.5 rounded-sm border border-line-subtle bg-bg-2 text-xs hover:bg-bg-3"
              >
                <RefreshCw size={12} /> Erneut laden
              </button>
            </div>
          </div>
        </Card>
      )}

      {list.state === "ready" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
          {list.data.agents.map((a) => (
            <AgentCard
              key={a.slug}
              agent={a}
              expanded={expanded === a.slug}
              onToggle={() => setExpanded((e) => (e === a.slug ? null : a.slug))}
              onCommandSent={list.reload}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function AgentCard({
  agent,
  expanded,
  onToggle,
  onCommandSent,
}: {
  agent: AgentSummary;
  expanded: boolean;
  onToggle: () => void;
  onCommandSent: () => void;
}) {
  const [detailReloadKey, setDetailReloadKey] = useState(0);
  const [tab, setTab] = useState<AgentTab>("chat");
  const triggerDetailReload = () => setDetailReloadKey((k) => k + 1);
  return (
    <Card padded>
      <CardHeader
        title={
          <div className="flex items-center gap-2">
            <span className="text-fg-subtle">{ICON_BY_SLUG[agent.slug] ?? <Bot size={16} />}</span>
            <span>{agent.name}</span>
          </div>
        }
        subtitle={
          agent.agent_id ? (
            <span className="font-mono text-2xs">{agent.agent_id}</span>
          ) : (
            <span className="text-2xs italic">interner Prozess (keine Agent-ID)</span>
          )
        }
        right={
          <Badge tone={STATUS_TONE[agent.status]} dot>
            {STATUS_LABEL[agent.status]}
          </Badge>
        }
      />

      <p className="text-xs text-fg-muted leading-relaxed">{agent.role}</p>

      <div className="mt-4 grid grid-cols-2 gap-3 text-2xs">
        <KV k="last_seen" v={agent.last_seen ? formatTs(agent.last_seen) : "—"} />
        <KV k="findings" v={String(agent.findings_count)} />
        <KV k="runs" v={String(agent.runs_count)} />
        <KV k="modes" v={agent.modes.join(", ")} />
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {agent.permissions.map((p) => (
          <span
            key={p}
            className="inline-flex items-center rounded-xs border border-line-subtle bg-bg-2 px-1.5 py-0.5 font-mono text-[10px] text-fg-subtle"
          >
            {p}
          </span>
        ))}
      </div>

      <div className="mt-3 text-2xs text-fg-subtle italic">{STATUS_HINT[agent.status]}</div>

      <div className="mt-4 flex items-center gap-1 border-b border-line-subtle">
        <TabButton active={tab === "chat"} onClick={() => setTab("chat")} icon={<MessageSquare size={12} />}>
          Chat
        </TabButton>
        <TabButton active={tab === "command"} onClick={() => setTab("command")} icon={<Terminal size={12} />}>
          Anweisung
        </TabButton>
        <TabButton
          active={tab === "details"}
          onClick={() => {
            setTab("details");
            if (!expanded) onToggle();
          }}
          icon={<FileText size={12} />}
        >
          Findings
        </TabButton>
      </div>

      {tab === "chat" && (
        <AgentChat
          slug={agent.slug}
          onSent={() => {
            onCommandSent();
            triggerDetailReload();
          }}
        />
      )}

      {tab === "command" && (
        <CommandComposer
          slug={agent.slug}
          modes={agent.modes}
          onSent={() => {
            onCommandSent();
            triggerDetailReload();
          }}
        />
      )}

      {tab === "details" && <AgentDetailSection slug={agent.slug} reloadKey={detailReloadKey} />}
    </Card>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 h-8 px-3 text-xs font-medium border-b-2 -mb-px transition-colors",
        active
          ? "border-accent text-fg"
          : "border-transparent text-fg-muted hover:text-fg hover:border-line",
      )}
    >
      {icon}
      {children}
    </button>
  );
}

const SOURCE_META: Record<AgentEventSource, { icon: React.ReactNode; label: string; tone: string }> = {
  dashboard: { icon: <Monitor size={10} />, label: "Dashboard", tone: "text-info" },
  telegram: { icon: <Smartphone size={10} />, label: "Telegram", tone: "text-accent" },
  agent: { icon: <Cpu size={10} />, label: "Agent", tone: "text-ai" },
};

function AgentChat({ slug, onSent }: { slug: string; onSent: () => void }) {
  const msg = useApi(
    (signal) => fetchAgentMessages(slug, { tail: 100 }, signal),
    5_000,
    [slug],
  );
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const events = msg.state === "ready" ? msg.data.events : [];
  const lastCount = useRef(0);

  useEffect(() => {
    if (events.length !== lastCount.current) {
      lastCount.current = events.length;
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [events.length]);

  async function send() {
    const content = draft.trim();
    if (!content || sending) return;
    setSending(true);
    setSendError(null);
    try {
      await postAgentMessage(slug, content);
      setDraft("");
      msg.reload();
      onSent();
    } catch (e) {
      setSendError(e instanceof Error ? e.message : "Unbekannter Fehler");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="mt-3 space-y-2">
      <div
        ref={scrollRef}
        className="border border-line-subtle rounded-sm bg-bg-0/50 h-[280px] overflow-y-auto p-2 space-y-2"
      >
        {msg.state === "loading" && (
          <p className="text-2xs text-fg-subtle italic">Lade Verlauf…</p>
        )}
        {msg.state === "error" && (
          <p className="text-2xs text-warn">Fehler: {msg.error.kind} ({msg.error.status})</p>
        )}
        {msg.state === "ready" && events.length === 0 && (
          <p className="text-2xs text-fg-subtle italic">
            Noch keine Nachrichten. Schreibe etwas — der Agent antwortet asynchron (Dashboard + Telegram teilen denselben Verlauf).
          </p>
        )}
        {events.map((e) => (
          <ChatBubble key={e.id} event={e} />
        ))}
      </div>

      <div className="flex items-end gap-2">
        <textarea
          value={draft}
          onChange={(ev) => setDraft(ev.target.value)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
              ev.preventDefault();
              void send();
            }
          }}
          placeholder="Nachricht an den Agenten…  (Strg+Enter senden)"
          rows={2}
          maxLength={4000}
          className="flex-1 rounded-sm border border-line-subtle bg-bg-2 text-xs text-fg p-2 placeholder:text-fg-subtle/70 focus:outline-none focus:border-line-strong focus:bg-bg-1 transition-colors resize-y min-h-[52px]"
        />
        <button
          onClick={send}
          disabled={sending || !draft.trim()}
          className={cn(
            "inline-flex items-center gap-1.5 h-10 px-3 rounded-sm border text-xs font-medium",
            "border-accent/30 bg-accent/10 text-accent hover:bg-accent/20",
            (sending || !draft.trim()) && "opacity-50 cursor-not-allowed",
          )}
        >
          {sending ? <RefreshCw size={12} className="animate-spin" /> : <Send size={12} />}
          Senden
        </button>
      </div>

      {sendError && (
        <p className="text-2xs text-neg">
          <AlertTriangle size={10} className="inline mr-1" />
          {sendError}
        </p>
      )}

      <div className="flex items-center justify-between text-2xs text-fg-subtle">
        <span>{draft.length}/4000 Zeichen · Auto-Refresh alle 5s</span>
        <button
          onClick={() => msg.reload()}
          className="inline-flex items-center gap-1 hover:text-fg"
        >
          <RefreshCw size={10} />
          Neu laden
        </button>
      </div>
    </div>
  );
}

function ChatBubble({ event }: { event: AgentEvent }) {
  const isOperator = event.role === "operator";
  const src = SOURCE_META[event.source] ?? SOURCE_META.dashboard;
  return (
    <div className={cn("flex", isOperator ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-md border px-2.5 py-1.5 text-xs",
          isOperator
            ? "bg-bg-2 border-line-subtle text-fg"
            : "bg-ai/5 border-ai/20 text-fg",
        )}
      >
        <div className="flex items-center gap-1.5 mb-1 text-2xs">
          <span className={cn("inline-flex items-center gap-1 font-mono", src.tone)}>
            {src.icon}
            {src.label}
          </span>
          {event.kind !== "message" && (
            <span className="rounded-xs border border-line-subtle bg-bg-1 px-1 font-mono text-[9px] uppercase tracking-wider text-fg-subtle">
              {event.kind}
            </span>
          )}
          <span className="ml-auto font-mono text-fg-subtle">{formatTs(event.ts).slice(5)}</span>
        </div>
        <div className="whitespace-pre-wrap break-words leading-relaxed">{event.content}</div>
      </div>
    </div>
  );
}

function CommandComposer({
  slug,
  modes,
  onSent,
}: {
  slug: string;
  modes: string[];
  onSent: () => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ mode: string; kind: "ok" | "err"; msg: string } | null>(null);

  async function send(mode: string) {
    if (busy) return;
    setBusy(mode);
    setResult(null);
    try {
      const r = await postAgentCommand(slug, mode, note.trim() || undefined);
      setResult({ mode, kind: "ok", msg: `Queued · id ${r.id.slice(0, 8)}` });
      setNote("");
      onSent();
      setTimeout(() => setResult(null), 4000);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Unbekannter Fehler";
      setResult({ mode, kind: "err", msg });
      setTimeout(() => setResult(null), 6000);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-4 space-y-2">
      <label className="block text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
        Anweisung (optional)
      </label>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="z.B. 'Prüfe Token-Drift in artifacts/alert_audit.jsonl der letzten 48h'"
        rows={2}
        maxLength={500}
        className="w-full rounded-sm border border-line-subtle bg-bg-2 text-xs text-fg p-2 font-mono placeholder:text-fg-subtle/70 focus:outline-none focus:border-line-strong focus:bg-bg-1 transition-colors resize-y min-h-[52px]"
      />
      <div className="flex items-center justify-between gap-2 text-2xs text-fg-subtle">
        <span>{note.length}/500 Zeichen</span>
        <span className="font-mono">POST → /operator/agents/{slug}/commands</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {modes.map((mode) => (
          <button
            key={mode}
            onClick={() => send(mode)}
            disabled={busy !== null}
            className={cn(
              "inline-flex items-center gap-1.5 h-8 px-3 rounded-sm border text-xs font-medium",
              "border-line-subtle bg-bg-2 text-fg hover:bg-bg-3 hover:border-line",
              busy === mode && "opacity-60 cursor-wait",
              busy !== null && busy !== mode && "opacity-40 cursor-not-allowed",
            )}
            title={`Mode '${mode}' an ${slug} schicken`}
          >
            {busy === mode ? (
              <RefreshCw size={12} className="animate-spin" />
            ) : (
              <Bot size={12} />
            )}
            <span className="font-mono">{mode}</span>
          </button>
        ))}
      </div>
      {result && (
        <div
          className={cn(
            "flex items-start gap-2 rounded-sm border px-2.5 py-1.5 text-2xs",
            result.kind === "ok"
              ? "border-pos/30 bg-pos/10 text-pos"
              : "border-neg/30 bg-neg/10 text-neg",
          )}
        >
          {result.kind === "ok" ? (
            <CheckCircle2 size={12} className="mt-0.5 shrink-0" />
          ) : (
            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          )}
          <span className="min-w-0 break-words">
            <strong className="font-mono">{result.mode}</strong> · {result.msg}
          </span>
        </div>
      )}
    </div>
  );
}

function AgentDetailSection({ slug, reloadKey }: { slug: string; reloadKey: number }) {
  const detail = useApi(
    (signal) => fetchAgentDetail(slug, signal),
    10_000,
    [slug, reloadKey],
  );

  return (
    <div className="mt-4 pt-4 border-t border-line-subtle space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-2xs text-fg-subtle">
          Auto-Refresh alle 10s
          {detail.state === "ready" && (
            <span className="ml-2 font-mono">· {detail.data.recent_findings.length} findings · {detail.data.recent_runs.length} runs</span>
          )}
        </span>
        <button
          onClick={() => detail.reload()}
          disabled={detail.state === "loading"}
          className="inline-flex items-center gap-1 h-6 px-2 rounded-sm border border-line-subtle bg-bg-2 text-2xs text-fg-muted hover:bg-bg-3 hover:text-fg disabled:opacity-50"
        >
          <RefreshCw size={10} className={cn(detail.state === "loading" && "animate-spin")} />
          Neu laden
        </button>
      </div>

      {detail.state === "loading" && <p className="text-2xs text-fg-subtle">Lade Detail…</p>}

      {detail.state === "error" && (
        <p className="text-2xs text-warn">
          Detail-Fehler: {detail.error.kind} ({detail.error.status})
        </p>
      )}

      {detail.state === "ready" && (
        <>
          <DetailList
            label={`Letzte Findings (${detail.data.recent_findings.length})`}
            empty="Noch keine Findings im Dropbox."
            rows={detail.data.recent_findings.map((f) => ({
              ts: (f.ts ?? f.timestamp ?? "") as string,
              title: (f.title ?? f.detail ?? JSON.stringify(f)) as string,
              tag: (f.severity ?? "") as string,
            }))}
          />
          <DetailList
            label={`Letzte Runs (${detail.data.recent_runs.length})`}
            empty="Noch keine Runs im Dropbox."
            rows={detail.data.recent_runs.map((r) => ({
              ts: (r.ts ?? r.timestamp ?? "") as string,
              title: `${r.mode ?? "?"} → ${r.result ?? "?"}`,
              tag: r.duration_ms != null ? `${r.duration_ms}ms` : "",
            }))}
          />
        </>
      )}
    </div>
  );
}

function DetailList({
  label,
  empty,
  rows,
}: {
  label: string;
  empty: string;
  rows: Array<{ ts: string; title: string; tag: string }>;
}) {
  return (
    <div>
      <div className="text-2xs font-semibold uppercase tracking-wider text-fg-subtle mb-1.5">
        {label}
      </div>
      {rows.length === 0 ? (
        <p className="text-2xs text-fg-subtle italic">{empty}</p>
      ) : (
        <ul className="space-y-1">
          {[...rows].reverse().slice(0, 12).map((r, i) => (
            <li key={i} className="flex items-start gap-2 text-2xs">
              <span className="font-mono text-fg-subtle shrink-0 w-[120px] truncate">
                {r.ts ? formatTs(r.ts) : "—"}
              </span>
              <span className="flex-1 min-w-0 text-fg-muted truncate">{r.title}</span>
              {r.tag && <span className="font-mono text-fg-subtle shrink-0">{r.tag}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between border-b border-line-subtle/50 py-1">
      <span className="font-mono text-fg-subtle">{k}</span>
      <span className="font-mono text-fg truncate ml-2">{v}</span>
    </div>
  );
}

function formatTs(ts: string): string {
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    return d.toISOString().slice(0, 16).replace("T", " ");
  } catch {
    return ts;
  }
}
