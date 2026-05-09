// KAI Live Widget — central system persona render.
// DALI Audit Layout: Hero-Strip oben (Full), Header-Anchor (Compact), Top-Stack vor KPI (Mobile).
// ERROR/WARNING brechen visuell durch — IDLE/ANALYSIS bleiben unauffaellig.
//
// DALI-P-068+P-070 (2026-05-08):
//   - Anfuehrungszeichen weg (war Hauptursache fuer "klatsch klatsch").
//   - Sprechblasen-Tail unter Avatar via .kai-bubble::before in kai.tokens.css.
//   - Type-On-Effekt ueber 60 Zeichen mit prefers-reduced-motion-Guard.
//   - Wave-Indicator (3 Pulse-Dots) waehrend Type-On.
//   - aria-live="polite" auf Comment fuer Screenreader.
//   - Cycle-Engine via cycleKaiPhrase: 60% State-Pool / 25% Quote / 15% Backend-Reuse.
//   - Anti-Repeat-History n=5 pro Session.
//   - Greeting 1x pro Session (sessionStorage).
//   - Pause bei OFFLINE/ERROR (Stille passender als Plauderei).
//
// Operator-Folge 2026-05-08:
//   - Greeting OHNE "Persona non grata" (steht im Header).
//   - Sprechblase wird zur Chat-History: max 5 Messages, Greeting + Cycle bleiben sichtbar.
//   - Aeltere Messages gedaempft (text-fg-muted, opacity), neueste in voller Farbe.
//   - Type-On + Wave nur auf der neuesten Message.
//   - Disabled Input-Stub als Vorbereitung fuer Phase 2 (Operator-Reply an KAI).

import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import type { KaiLiveWidgetProps } from "../../kai/types";
import { cycleKaiPhrase, getGreeting, isPhraseSafe } from "../../kai/phraseEngine";
import { KaiAvatar } from "./KaiAvatar";
import { KaiStatusBadge } from "./KaiStatusBadge";

const LANG_LOCALE = { de: "de-DE", en: "en-US" } as const;
const HISTORY_LEN = 5;
const MAX_VISIBLE_MESSAGES = 5;
const CYCLE_MIN_MS = 45_000;
const CYCLE_MAX_MS = 90_000;
const TYPE_ON_BUDGET_MS = 600;
const TYPE_ON_MAX_CHARS = 60;
const GREETING_KEY = "kai_greeted_at";

type ChatOrigin = "greeting" | "cycle" | "backend";

interface ChatEntry {
  id: string;
  text: string;
  origin: ChatOrigin;
}

let __idCounter = 0;
function makeId(): string {
  __idCounter += 1;
  return `kai-${Date.now()}-${__idCounter}`;
}

function formatTs(ts: string, language: "de" | "en"): string {
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toLocaleString(LANG_LOCALE[language], {
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  } catch {
    return ts;
  }
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function shouldPauseCycle(state: string): boolean {
  // Bei OFFLINE/ERROR ist Stille passender als Plauderei.
  return state === "OFFLINE" || state === "ERROR";
}

// Singleton: Greeting nur 1x pro Session anzeigen.
function shouldRenderGreeting(state: string): boolean {
  if (state !== "IDLE") return false;
  try {
    if (sessionStorage.getItem(GREETING_KEY)) return false;
    sessionStorage.setItem(GREETING_KEY, new Date().toISOString());
    return true;
  } catch {
    return false;
  }
}

// Type-On-Hook: respektiert prefers-reduced-motion + state-pause.
function useTypeOn(
  text: string,
  enabled: boolean,
): { rendered: string; isTyping: boolean } {
  const [rendered, setRendered] = useState(text);
  const [isTyping, setIsTyping] = useState(false);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    if (!enabled || !text) {
      setRendered(text);
      setIsTyping(false);
      return;
    }

    const limit = Math.min(text.length, TYPE_ON_MAX_CHARS);
    const start = performance.now();
    setIsTyping(true);
    setRendered("");

    const tick = (now: number) => {
      const elapsed = now - start;
      const ratio = Math.min(1, elapsed / TYPE_ON_BUDGET_MS);
      const cutoff = Math.floor(ratio * limit);
      // Tippe die ersten `limit` Zeichen progressiv, Rest snappt am Ende.
      if (cutoff < limit) {
        setRendered(text.substring(0, cutoff));
        rafRef.current = requestAnimationFrame(tick);
      } else {
        setRendered(text);
        setIsTyping(false);
        rafRef.current = null;
      }
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [text, enabled]);

  return { rendered, isTyping };
}

export function KaiLiveWidget(props: KaiLiveWidgetProps) {
  const {
    runtimeState,
    lastSignal,
    lastWarning,
    agentStatuses = [],
    compact = false,
    language = "de",
    onOpenAuditLog,
    onOpenDetails,
  } = props;

  const ts = useMemo(
    () => formatTs(runtimeState.timestamp, language),
    [runtimeState.timestamp, language],
  );

  // Anti-Repeat-History fuer cycleKaiPhrase: zwei Listen.
  // textHistoryRef = letzte n=5 Phrases (verhindert woertliche Wiederholung).
  // themeHistoryRef = letzte n=2 Themes (verhindert thematische Cluster wie
  // "zwei Buffett-Quotes hintereinander" oder "drei Watchful-IDLE-Saetze").
  const textHistoryRef = useRef<string[]>([]);
  const themeHistoryRef = useRef<string[]>([]);
  const THEME_HISTORY_LEN = 2;

  // Chat-History: Greeting + Cycle-Phrases + Backend-Comments. Max 5 sichtbar.
  // Greeting wird bei initialem IDLE einmal pro Session vorangestellt.
  const [messages, setMessages] = useState<ChatEntry[]>(() => {
    const init: ChatEntry[] = [];
    const greet = shouldRenderGreeting(runtimeState.state) ? getGreeting(language) : null;
    if (greet) {
      init.push({ id: makeId(), text: greet, origin: "greeting" });
      textHistoryRef.current = [greet];
      themeHistoryRef.current = ["greeting"];
    } else if (runtimeState.comment) {
      init.push({ id: makeId(), text: runtimeState.comment, origin: "backend" });
      textHistoryRef.current = [runtimeState.comment];
      themeHistoryRef.current = ["backend"];
    }
    return init;
  });

  // Cycle: alle 45-90s neue Phrase appenden. Pause bei OFFLINE/ERROR.
  useEffect(() => {
    if (shouldPauseCycle(runtimeState.state)) return;
    let cancelled = false;

    function tick() {
      if (cancelled) return;
      const next = cycleKaiPhrase({
        state: runtimeState.state,
        language,
        textHistory: textHistoryRef.current,
        themeHistory: themeHistoryRef.current,
        backendComment: runtimeState.comment,
      });
      if (!isPhraseSafe(next.text, language)) return;
      textHistoryRef.current = [next.text, ...textHistoryRef.current].slice(0, HISTORY_LEN);
      themeHistoryRef.current = [next.theme, ...themeHistoryRef.current].slice(0, THEME_HISTORY_LEN);
      const entry: ChatEntry = { id: makeId(), text: next.text, origin: "cycle" };
      setMessages((prev) => [...prev, entry].slice(-MAX_VISIBLE_MESSAGES));
    }

    function schedule(): number {
      const jitter = CYCLE_MIN_MS + Math.random() * (CYCLE_MAX_MS - CYCLE_MIN_MS);
      return window.setTimeout(() => {
        tick();
        schedule();
      }, jitter);
    }
    const id = schedule();
    return () => {
      cancelled = true;
      window.clearTimeout(id);
    };
  }, [runtimeState.state, language]);

  // Backend-Comment-Echo: wenn Backend einen neuen Comment liefert
  // (z.B. nach State-Change oder echter Phrase-Aktualisierung), an die
  // History anhaengen. Dedupe gegen letzte Message.
  useEffect(() => {
    if (!runtimeState.comment) return;
    setMessages((prev) => {
      const echo: ChatEntry = {
        id: makeId(),
        text: runtimeState.comment,
        origin: "backend",
      };
      if (prev.length === 0) return [echo];
      const last = prev[prev.length - 1];
      if (last.text === runtimeState.comment) return prev;
      return [...prev, echo].slice(-MAX_VISIBLE_MESSAGES);
    });
    textHistoryRef.current = [runtimeState.comment, ...textHistoryRef.current].slice(0, HISTORY_LEN);
    themeHistoryRef.current = ["backend", ...themeHistoryRef.current].slice(0, THEME_HISTORY_LEN);
  }, [runtimeState.comment]);

  // Type-On nur auf der neuesten Message. Pause bei OFFLINE/ERROR
  // -> Type-On-Effekt aus, Text snappt direkt.
  const latestMessage = messages.length > 0 ? messages[messages.length - 1] : null;
  const typeOnEnabled = !prefersReducedMotion() && !shouldPauseCycle(runtimeState.state);
  const { rendered: latestDisplay, isTyping } = useTypeOn(
    latestMessage?.text ?? "",
    typeOnEnabled,
  );

  if (compact) {
    return (
      <div
        className={cn(
          "kai-widget kai-widget--compact",
          `kai-widget--state-${runtimeState.state}`,
          "flex items-center gap-2",
        )}
        aria-label="KAI compact"
      >
        <KaiAvatar state={runtimeState.state} size="compact" />
        <KaiStatusBadge state={runtimeState.state} />
      </div>
    );
  }

  return (
    <section
      className={cn(
        "kai-widget kai-widget--full kai-card",
        `kai-widget--state-${runtimeState.state}`,
        "p-4 synthwave-edge scanline-overlay",
      )}
      aria-label="KAI Live Widget"
    >
      <header className="flex items-center justify-between gap-4 mb-3">
        <div className="flex items-center gap-3">
          <KaiAvatar state={runtimeState.state} size="full" />
          <div>
            <div className="text-sm uppercase tracking-widest font-bold text-fg">KAI LIVE</div>
            <div className="text-2xs uppercase tracking-wider text-fg-subtle">
              Persona non grata
            </div>
          </div>
        </div>
        <KaiStatusBadge state={runtimeState.state} />
      </header>

      <div className="kai-widget__body space-y-2">
        {/* Sprechblase als Chat-History: alle Messages sichtbar, neueste mit
            Type-On + Wave, aeltere gedaempft. Tail-Indicator ist am Container,
            nicht pro Message. */}
        <div className="kai-bubble flex flex-col gap-1">
          {messages.length === 0 && (
            <p className="text-sm leading-relaxed text-fg-subtle italic">…</p>
          )}
          {messages.map((m, idx) => {
            const isLatest = idx === messages.length - 1;
            const isOlder = idx < messages.length - 2;
            return (
              <p
                key={m.id}
                className={cn(
                  "text-sm leading-relaxed transition-opacity",
                  isLatest ? "text-fg" : "text-fg-muted",
                  isOlder && "opacity-55",
                )}
                aria-live={isLatest ? "polite" : undefined}
                aria-atomic={isLatest ? "true" : undefined}
              >
                <span className="kai-comment-text">
                  {isLatest ? latestDisplay : m.text}
                </span>
                {isLatest && isTyping && (
                  <span
                    aria-hidden="true"
                    className="inline-flex ml-1.5 align-middle gap-0.5"
                  >
                    <span className="kai-wave-dot" />
                    <span className="kai-wave-dot" style={{ animationDelay: "120ms" }} />
                    <span className="kai-wave-dot" style={{ animationDelay: "240ms" }} />
                  </span>
                )}
              </p>
            );
          })}
        </div>

        {/* Phase 2 — Operator-Reply an KAI. Heute disabled, aber sichtbar als
            Architektur-Anker, damit der Chat-Charakter der Sprechblase erkennbar ist. */}
        <form
          className="kai-reply-form pt-1"
          onSubmit={(e) => {
            e.preventDefault();
          }}
          aria-label="An KAI antworten"
        >
          <input
            type="text"
            disabled
            placeholder="An KAI antworten — bald verfügbar"
            aria-label="Antwort an KAI"
            className="w-full bg-bg-2/60 border border-line-subtle rounded-md px-3 py-1.5 text-xs text-fg-subtle placeholder:text-fg-subtle/70 cursor-not-allowed focus:outline-none"
          />
        </form>

        <p className="text-xs text-fg-subtle font-mono">{ts}</p>

        {lastSignal && (
          <div className="kai-mini-card kai-mini-card--signal mt-3 p-2 rounded-md border border-fg-subtle/30 text-xs">
            <strong className="text-fg-muted">Last Signal: </strong>
            <span className="font-mono">
              {lastSignal.asset} · {lastSignal.direction} · {lastSignal.confidence}% · Risk {lastSignal.risk}
            </span>
          </div>
        )}

        {lastWarning && (
          <div className="kai-mini-card kai-mini-card--warning mt-2 p-2 rounded-md border border-fg-subtle/30 text-xs">
            <strong className="text-fg-muted">Last Warning: </strong>
            <span>
              {lastWarning.target} · {lastWarning.risk} · {lastWarning.problem}
            </span>
          </div>
        )}

        {agentStatuses.length > 0 && (
          <div className="kai-agent-row flex flex-wrap gap-1.5 mt-3">
            {agentStatuses.slice(0, 6).map((agent) => (
              <span
                key={agent.agent}
                className={cn(
                  "px-1.5 py-0.5 text-[10px] uppercase tracking-wide font-mono rounded-sm border",
                  agent.status === "OK" && "border-pos/40 text-pos",
                  agent.status === "WARNING" && "border-warn/40 text-warn",
                  agent.status === "ERROR" && "border-neg/40 text-neg",
                  agent.status === "OFFLINE" && "border-fg-subtle/30 text-fg-subtle",
                  agent.status === "UNKNOWN" && "border-fg-subtle/30 text-fg-subtle italic",
                )}
                title={agent.summary}
              >
                {agent.agent}: {agent.status}
              </span>
            ))}
          </div>
        )}
      </div>

      <footer className="mt-3 flex items-center justify-between gap-2">
        {runtimeState.nextAction && (
          <span className="text-xs text-fg-muted">{runtimeState.nextAction}</span>
        )}
        <div className="flex gap-2 ml-auto">
          <button
            type="button"
            onClick={onOpenDetails ?? (() => undefined)}
            disabled={!onOpenDetails}
            className="text-xs px-2 py-1 rounded-md border border-fg-subtle/30 hover:border-fg-subtle text-fg-muted hover:text-fg disabled:opacity-50 disabled:cursor-not-allowed"
            title={onOpenDetails ? "Details oeffnen" : "Details (Phase 2)"}
          >
            Details
          </button>
          <button
            type="button"
            onClick={onOpenAuditLog ?? (() => undefined)}
            disabled={!onOpenAuditLog}
            className="text-xs px-2 py-1 rounded-md border border-fg-subtle/30 hover:border-fg-subtle text-fg-muted hover:text-fg disabled:opacity-50 disabled:cursor-not-allowed"
            title={onOpenAuditLog ? "Audit-Log oeffnen" : "Audit (Phase 2)"}
          >
            Audit
          </button>
        </div>
      </footer>
    </section>
  );
}
