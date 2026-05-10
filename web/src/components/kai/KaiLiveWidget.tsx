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
import { Mic, Send, Loader2, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import type { KaiLiveWidgetProps } from "../../kai/types";
import { cycleKaiPhrase, getGreeting, isPhraseSafe } from "../../kai/phraseEngine";
import { KaiAvatar } from "./KaiAvatar";
import { KaiStatusBadge } from "./KaiStatusBadge";

const LANG_LOCALE = { de: "de-DE", en: "en-US" } as const;
const HISTORY_LEN = 5;
// 2026-05-09 Phase 2: 5 -> 8. Sprechblase rendert jetzt Operator-Fragen + KAI-Antworten zusätzlich
// zu Cycle-Phrasen. DALI-Audit-Empfehlung nach Quote-Kürzung.
const MAX_VISIBLE_MESSAGES = 8;
const CYCLE_MIN_MS = 45_000;
const CYCLE_MAX_MS = 90_000;
const TYPE_ON_BUDGET_MS = 600;
const TYPE_ON_MAX_CHARS = 60;
const GREETING_KEY = "kai_greeted_at";

type ChatOrigin = "greeting" | "cycle" | "backend" | "operator" | "reply";

const KAI_CHAT_ENDPOINT = "/api/kai/chat";
const KAI_TRANSCRIBE_ENDPOINT = "/api/kai/transcribe";

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

// Greeting 1x pro Operator-Tag (UTC), persistent über localStorage.
// Vorher: sessionStorage → 4 offene Tabs = 4 Greetings, kein Tages-Refresh.
// Storage-Key kai_greeted_at speichert ISO-Timestamp; wir matchen auf YYYY-MM-DD.
function shouldRenderGreeting(state: string): boolean {
  if (state !== "IDLE") return false;
  try {
    const last = localStorage.getItem(GREETING_KEY);
    const today = new Date().toISOString().slice(0, 10);
    if (last && last.startsWith(today)) return false;
    localStorage.setItem(GREETING_KEY, new Date().toISOString());
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

  // 2026-05-09 Phase 2.3: MediaRecorder + Whisper-Backend ersetzt Web Speech API.
  // Web Speech API fiel auf Mobile-Browsern (Brave/Firefox/Samsung) durch
  // [not-allowed] aus. MediaRecorder + Backend-Whisper läuft in jedem Browser.
  const [inputValue, setInputValue] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const mediaStreamRef = useRef<MediaStream | null>(null);

  // Voice-Support-Check: MediaRecorder + getUserMedia. Funktioniert in JEDEM
  // modernen Browser inkl. Mobile Brave/Firefox/Samsung Internet.
  const voiceSupported =
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof window !== "undefined" &&
    typeof window.MediaRecorder !== "undefined";

  useEffect(() => {
    if (!isListening) {
      setRecordingSeconds(0);
      return;
    }
    setRecordingSeconds(0);
    const id = window.setInterval(() => {
      setRecordingSeconds((s) => s + 1);
    }, 1000);
    return () => window.clearInterval(id);
  }, [isListening]);

  // Aufräumen: MediaStream-Tracks beim Unmount sicher schließen (sonst bleibt
  // das Mic-Indicator im Browser-Tab aktiv).
  useEffect(() => {
    return () => {
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
      try {
        mediaRecorderRef.current?.stop();
      } catch {
        /* ignore */
      }
    };
  }, []);

  const submitOperatorMessage = async (rawText: string): Promise<void> => {
    const text = rawText.trim();
    if (!text || isSending) return;
    setIsSending(true);
    setInputValue("");

    const opEntry: ChatEntry = { id: makeId(), text, origin: "operator" };
    setMessages((prev) => [...prev, opEntry].slice(-MAX_VISIBLE_MESSAGES));

    try {
      const res = await fetch(KAI_CHAT_ENDPOINT, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, language }),
      });
      if (!res.ok) throw new Error(`http_${res.status}`);
      const data = await res.json();
      const replyText = (typeof data?.reply === "string" && data.reply) || "Keine Antwort.";
      const replyEntry: ChatEntry = { id: makeId(), text: replyText, origin: "reply" };
      setMessages((prev) => [...prev, replyEntry].slice(-MAX_VISIBLE_MESSAGES));
    } catch (err) {
      const fallbackText =
        language === "de"
          ? "KAI nicht erreichbar. Versuch es nochmal."
          : "KAI unreachable. Try again.";
      const errEntry: ChatEntry = { id: makeId(), text: fallbackText, origin: "reply" };
      setMessages((prev) => [...prev, errEntry].slice(-MAX_VISIBLE_MESSAGES));
    } finally {
      setIsSending(false);
    }
  };

  // 2026-05-09 Phase 2.3: MediaRecorder + Backend-Whisper.
  // getUserMedia → MediaRecorder → Audio-Blob → POST /api/kai/transcribe → Text → Textarea.
  // Funktioniert in jedem Browser inkl. Brave Mobile, Firefox, Samsung Internet.
  const startListening = async (): Promise<void> => {
    if (!voiceSupported || isListening || isTranscribing) return;

    // Telegram-WebView (Mobile): "tgWebApp" / "Telegram" im User-Agent.
    // Diese WebViews verweigern Mic-Permission systematisch — kein Browser-Setting
    // hilft. Operator muss den Link extern öffnen (Chrome/Safari).
    const ua = navigator.userAgent || "";
    const isTelegramWebView = /telegram|tgwebapp/i.test(ua);
    // Sonstige In-App-Browser, die typischerweise Mic blocken: Instagram, Facebook, LinkedIn.
    const isOtherInAppBrowser = /\b(FBAN|FBAV|Instagram|LinkedInApp)\b/.test(ua);

    let stream: MediaStream;
    try {
      // Constraints für bessere Audio-Qualität: Echo aus, Rauschen runter, Auto-Gain.
      // Wichtig für Firefox-Mic, das ohne diese oft sehr leise/dumpf aufnimmt.
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (exc) {
      const code = (exc as { name?: string })?.name || "unknown";
      // Spezial-Hinweis bei NotAllowedError. Wir geben den In-App-Browser-Tipp
      // IMMER mit, weil UA-Detection unzuverlässig ist (Telegram-WebView setzt
      // den UA-String nicht konsistent als "Telegram"). Lieber einmal zu viel
      // Hinweis als ein Operator der ratlos bleibt.
      let specialHint: string | null = null;
      if (code === "NotAllowedError" || code === "SecurityError") {
        if (isTelegramWebView) {
          specialHint = language === "de"
            ? "Telegram-Browser blockiert Mikrofon systematisch. Den Link extern in Chrome/Safari öffnen (lange auf Link drücken → 'Im Browser öffnen')."
            : "Telegram in-app browser blocks microphone. Open the link externally in Chrome/Safari (long-press the link → 'Open in browser').";
        } else if (isOtherInAppBrowser) {
          specialHint = language === "de"
            ? "Dieser In-App-Browser blockiert Mikrofon. Link extern in Chrome/Safari öffnen."
            : "This in-app browser blocks microphone. Open the link externally in Chrome/Safari.";
        } else {
          specialHint = language === "de"
            ? "Mikrofon blockiert. Falls du im Telegram/Instagram/Facebook-Chat bist: Link lange drücken → 'Im Browser öffnen'. Sonst Browser-Site-Settings für kai-trader.org prüfen."
            : "Microphone blocked. If you are inside Telegram/Instagram/Facebook chat: long-press the link → 'Open in browser'. Otherwise check browser site settings for kai-trader.org.";
        }
      }
      const HINT_DE: Record<string, string> = {
        NotAllowedError: "Mikrofon-Zugriff verweigert. In den Browser-Site-Settings für kai-trader.org erlauben.",
        SecurityError: "Mikrofon-Zugriff blockiert (Security-Policy). HTTPS prüfen.",
        NotFoundError: "Kein Mikrofon gefunden. Hardware verbunden?",
        NotReadableError: "Mikrofon belegt von anderer App/Tab.",
        OverconstrainedError: "Mikrofon-Konfiguration nicht erfüllbar.",
        AbortError: "Anfrage abgebrochen.",
      };
      const HINT_EN: Record<string, string> = {
        NotAllowedError: "Microphone access denied. Allow it in browser site settings.",
        SecurityError: "Microphone blocked (security policy). Check HTTPS.",
        NotFoundError: "No microphone found. Hardware connected?",
        NotReadableError: "Microphone busy (another app/tab).",
        OverconstrainedError: "Microphone constraints unmet.",
        AbortError: "Request aborted.",
      };
      const hint = specialHint || (language === "de" ? HINT_DE : HINT_EN)[code];
      const errMsg =
        language === "de"
          ? `Spracheingabe fehlgeschlagen [${code}]${hint ? " — " + hint : ""}`
          : `Voice input failed [${code}]${hint ? " — " + hint : ""}`;
      // eslint-disable-next-line no-console
      console.warn("[KAI Voice] getUserMedia error:", code, "ua:", ua, "tg:", isTelegramWebView, exc);
      const errEntry: ChatEntry = { id: makeId(), text: errMsg, origin: "reply" };
      setMessages((prev) => [...prev, errEntry].slice(-MAX_VISIBLE_MESSAGES));
      return;
    }

    mediaStreamRef.current = stream;
    audioChunksRef.current = [];

    // mimeType-Detection: Browser unterstützen unterschiedliche Codecs.
    // Whisper akzeptiert webm/ogg/mp4/m4a — wir wählen den ersten, den der Browser kann.
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"];
    const supportedType = candidates.find((t) => {
      try {
        return MediaRecorder.isTypeSupported(t);
      } catch {
        return false;
      }
    });

    let recorder: MediaRecorder;
    try {
      recorder = supportedType
        ? new MediaRecorder(stream, { mimeType: supportedType })
        : new MediaRecorder(stream);
    } catch (exc) {
      // eslint-disable-next-line no-console
      console.warn("[KAI Voice] MediaRecorder ctor failed:", exc);
      stream.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
      const errEntry: ChatEntry = {
        id: makeId(),
        text: language === "de"
          ? "MediaRecorder im Browser nicht initialisierbar."
          : "MediaRecorder could not initialize in this browser.",
        origin: "reply",
      };
      setMessages((prev) => [...prev, errEntry].slice(-MAX_VISIBLE_MESSAGES));
      return;
    }

    recorder.ondataavailable = (ev: BlobEvent) => {
      if (ev.data && ev.data.size > 0) {
        audioChunksRef.current.push(ev.data);
      }
    };

    recorder.onstop = async () => {
      // Mic-Tracks freigeben — Tab-Indicator geht weg.
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;

      const chunks = audioChunksRef.current;
      audioChunksRef.current = [];
      if (chunks.length === 0) {
        setIsListening(false);
        return;
      }

      const mimeType = recorder.mimeType || supportedType || "audio/webm";
      const blob = new Blob(chunks, { type: mimeType });
      // Min-Size-Schutz: <8KB → Whisper halluziniert YouTube-Untertitel-Phrasen
      // ("Untertitel der Amara.org-Community"). 8KB ≈ 1.5s reales Audio bei
      // Opus-Codec. Backend filtert Halluzinationen zusätzlich (defense-in-depth).
      if (blob.size < 8192) {
        // eslint-disable-next-line no-console
        console.warn("[KAI Voice] audio too short, skipping transcribe:", blob.size, "bytes");
        const hint: ChatEntry = {
          id: makeId(),
          text: language === "de"
            ? `Aufnahme zu kurz (${blob.size} Bytes). Mindestens 2 Sekunden klar sprechen, sonst halluziniert die Spracherkennung.`
            : `Recording too short (${blob.size} bytes). Speak at least 2 seconds clearly, otherwise speech recognition hallucinates.`,
          origin: "reply",
        };
        setMessages((prev) => [...prev, hint].slice(-MAX_VISIBLE_MESSAGES));
        setIsListening(false);
        return;
      }
      // eslint-disable-next-line no-console
      console.info("[KAI Voice] sending to /transcribe:", blob.size, "bytes,", mimeType);

      const ext = mimeType.includes("mp4") ? "mp4" : mimeType.includes("ogg") ? "ogg" : "webm";
      setIsListening(false);
      setIsTranscribing(true);
      try {
        const form = new FormData();
        form.append("audio", blob, `voice.${ext}`);
        form.append("language", language);
        const res = await fetch(KAI_TRANSCRIBE_ENDPOINT, {
          method: "POST",
          credentials: "include",
          body: form,
        });
        if (res.status === 413) {
          const errEntry: ChatEntry = {
            id: makeId(),
            text: language === "de"
              ? `Aufnahme zu groß (${(blob.size / 1024 / 1024).toFixed(1)} MB). Kürzer sprechen.`
              : `Recording too large (${(blob.size / 1024 / 1024).toFixed(1)} MB). Speak shorter.`,
            origin: "reply",
          };
          setMessages((prev) => [...prev, errEntry].slice(-MAX_VISIBLE_MESSAGES));
          return;
        }
        if (res.status === 401 || res.status === 403) {
          const errEntry: ChatEntry = {
            id: makeId(),
            text: language === "de"
              ? "Auth-Fehler beim Server. Cookie/Session abgelaufen — Seite neu laden."
              : "Server auth error. Cookie/session expired — reload page.",
            origin: "reply",
          };
          setMessages((prev) => [...prev, errEntry].slice(-MAX_VISIBLE_MESSAGES));
          return;
        }
        if (!res.ok) throw new Error(`http_${res.status}`);
        const data = await res.json();
        const text = (typeof data?.text === "string" && data.text.trim()) || "";
        if (text) {
          // 2026-05-09 Phase 2.7: Auto-Submit nach Voice. Operator-Wunsch:
          // Sprechen → automatisch an KAI senden, kein extra Klick auf "Senden".
          // Vorhandener Textarea-Inhalt wird vorangestellt (selten, aber möglich).
          setInputValue((prevInput) => {
            const composed = prevInput ? prevInput + " " + text : text;
            void submitOperatorMessage(composed);
            return "";
          });
        } else {
          // Whisper hat nichts erkannt — leise Hinweis.
          const hintEntry: ChatEntry = {
            id: makeId(),
            text: language === "de"
              ? "Nichts verstanden. Lauter oder näher zum Mikrofon."
              : "Nothing recognized. Speak louder or closer to the mic.",
            origin: "reply",
          };
          setMessages((prev) => [...prev, hintEntry].slice(-MAX_VISIBLE_MESSAGES));
        }
      } catch (exc) {
        // eslint-disable-next-line no-console
        console.warn("[KAI Voice] transcribe failed:", exc);
        const errEntry: ChatEntry = {
          id: makeId(),
          text: language === "de"
            ? "Transkription fehlgeschlagen. Server nicht erreichbar?"
            : "Transcription failed. Server unreachable?",
          origin: "reply",
        };
        setMessages((prev) => [...prev, errEntry].slice(-MAX_VISIBLE_MESSAGES));
      } finally {
        setIsTranscribing(false);
      }
    };

    mediaRecorderRef.current = recorder;
    setIsListening(true);
    try {
      // 100ms-Timeslice → kleine Chunks regelmäßig, statt einer großen am Ende.
      recorder.start(100);
    } catch (exc) {
      // eslint-disable-next-line no-console
      console.warn("[KAI Voice] recorder.start failed:", exc);
      stream.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
      mediaRecorderRef.current = null;
      setIsListening(false);
    }
  };

  const stopListening = (): void => {
    const rec = mediaRecorderRef.current;
    if (!rec) return;
    try {
      if (rec.state !== "inactive") rec.stop();
    } catch {
      /* ignore — onstop räumt auf */
    }
    mediaRecorderRef.current = null;
  };

  // 2026-05-09 Phase 2.1: Press-and-Hold via Pointer-Events (Touch + Mouse einheitlich).
  // 2026-05-09 Phase 2.8: PURE CLICK-TOGGLE.
  // Push-and-Hold raus — Browser feuern pointerup oft schon bei minimal
  // angehobenem Finger, was die Aufnahme ungewollt schneidet (Operator-Bug
  // "zu kurz"). Click-Toggle ist robust: 1 Klick startet, 1 Klick stoppt,
  // Operator kann beliebig lange sprechen. Plus: 90s auto-stop als Safety.
  const handleMicToggle = (): void => {
    if (!voiceSupported) {
      const msg =
        language === "de"
          ? "Mikro geht in diesem Browser nicht. Chrome oder Safari nehmen."
          : "Mic does not work here. Use Chrome or Safari.";
      const entry: ChatEntry = { id: makeId(), text: msg, origin: "reply" };
      setMessages((prev) => [...prev, entry].slice(-MAX_VISIBLE_MESSAGES));
      return;
    }
    if (isSending || isTranscribing) return;
    if (isListening) {
      stopListening();
    } else {
      void startListening();
    }
  };

  // Safety: nach 90s automatisch stoppen, falls Operator vergessen hat.
  // Whisper-Hard-Limit ist 25 MB ≈ 25-30 Min Audio bei opus, aber wir kappen
  // bewusst eine Größenordnung darunter — vernünftige Frage = max 90s.
  useEffect(() => {
    if (!isListening) return;
    const id = window.setTimeout(() => {
      // eslint-disable-next-line no-console
      console.warn("[KAI Voice] 90s safety auto-stop");
      stopListening();
    }, 90_000);
    return () => window.clearTimeout(id);
  }, [isListening]);

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

        {/* Phase 2.1 (2026-05-09) — DALI-redesigned Operator-Chat-Footer.
            Prominentes Textfeld + Telegram-Style runde Buttons mit Press-and-Hold am Mic. */}
        <form
          className="kai-reply-form pt-2 flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void submitOperatorMessage(inputValue);
          }}
          aria-label={language === "de" ? "An KAI schreiben" : "Send to KAI"}
        >
          <div className="relative flex-1 group">
            {/* 80er-Neon-Edge in IDLE-Cyan (#00B8D9, kongruent zu KAI-Avatar-Border). */}
            <textarea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void submitOperatorMessage(inputValue);
                }
              }}
              disabled={isSending}
              rows={2}
              placeholder={
                language === "de" ? "Frag KAI etwas …" : "Ask KAI something …"
              }
              aria-label={language === "de" ? "Nachricht an KAI" : "Message to KAI"}
              className={cn(
                "w-full resize-none rounded-xl px-3.5 py-2.5",
                "bg-bg-1 text-sm text-fg placeholder:text-[#00B8D9]/60",
                "border border-[#00B8D9]/40",
                "shadow-[inset_0_1px_2px_rgba(0,0,0,0.45),0_0_12px_-4px_rgba(0,184,217,0.5)]",
                "focus:outline-none focus:border-[#00B8D9]/80",
                "focus:shadow-[inset_0_1px_2px_rgba(0,0,0,0.45),0_0_18px_-2px_rgba(0,184,217,0.75),0_0_4px_rgba(0,184,217,0.4)]",
                "transition-[border-color,box-shadow] duration-200",
                "disabled:opacity-50 disabled:cursor-not-allowed",
                "min-h-[56px] font-mono tracking-wide",
              )}
            />
          </div>

          {/* 2026-05-09 Phase 2.3: Mic-Button IMMER rendern. Wenn !voiceSupported,
              disabled-Look + Click postet konkreten Hinweis welche Browser gehen.
              Vorher war der Button auf Laptop (Firefox/Brave) unsichtbar — Operator hatte
              keine Chance zu verstehen warum die Voice-Spalte fehlt. */}
          <button
            type="button"
            onClick={handleMicToggle}
            disabled={isSending || isTranscribing}
            aria-pressed={isListening}
            aria-label={
              !voiceSupported
                ? language === "de" ? "Spracheingabe nicht verfügbar (Browser)" : "Voice not supported (browser)"
                : isListening
                ? language === "de" ? `Aufnahme läuft seit ${recordingSeconds} Sekunden, Klick zum Stoppen` : `Recording for ${recordingSeconds} seconds, click to stop`
                : language === "de" ? "Aufnahme starten" : "Start recording"
            }
            title={
              !voiceSupported
                ? language === "de" ? "Spracheingabe nicht verfügbar" : "Voice not supported"
                : isListening
                ? language === "de" ? `Stoppen (${recordingSeconds}s)` : `Stop (${recordingSeconds}s)`
                : language === "de" ? "Sprachaufnahme starten" : "Start voice recording"
            }
            className={cn(
              "relative shrink-0 h-11 w-11 rounded-full",
              "flex items-center justify-center",
              "transition-all duration-150 select-none touch-none",
              "bg-bg-0/60 backdrop-blur-sm",
              !voiceSupported
                ? "text-fg-subtle border border-fg-subtle/30 cursor-help"
                : isTranscribing
                ? "text-[#00B8D9] border border-[#00B8D9]/70 shadow-[inset_0_0_0_1px_rgba(0,184,217,0.3),0_0_22px_-2px_rgba(0,184,217,0.55)]"
                : isListening
                ? "text-neg border border-neg/80 glow-neg animate-pulse"
                : "text-[#00B8D9] border border-[#00B8D9]/70 shadow-[inset_0_0_0_1px_rgba(0,184,217,0.3),0_0_22px_-2px_rgba(0,184,217,0.55)] hover:border-[#00B8D9] active:scale-95",
              "disabled:opacity-40 disabled:cursor-not-allowed",
            )}
          >
            {isTranscribing ? (
              <Loader2 className="h-[18px] w-[18px] animate-spin" aria-hidden="true" />
            ) : isListening ? (
              <Square className="h-[16px] w-[16px] fill-current" aria-hidden="true" strokeWidth={2.25} />
            ) : (
              <Mic className="h-[18px] w-[18px]" aria-hidden="true" strokeWidth={2.25} />
            )}
            {!voiceSupported && (
              <span
                aria-hidden="true"
                className="absolute inset-0 flex items-center justify-center"
              >
                <span className="block h-7 w-[2px] rotate-45 bg-fg-subtle/70" />
              </span>
            )}
            {isListening && (
              <>
                {/* Outer expanding ring — Telegram-Recording-Vorbild im Neon-Stil */}
                <span
                  aria-hidden="true"
                  className="absolute inset-0 rounded-full ring-2 ring-neg/70 animate-ping"
                />
                {/* Recording-Dot rechts oben — leuchtend statt deckend */}
                <span
                  aria-hidden="true"
                  className="absolute -top-1 -right-1 h-2.5 w-2.5 rounded-full bg-neg shadow-[0_0_8px_rgb(var(--neg)/0.9),0_0_14px_rgb(var(--neg)/0.5)]"
                />
                {/* Prominenter Recording-Status: REC + Sekunden, gut lesbar */}
                <span
                  aria-hidden="true"
                  className="absolute -bottom-6 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-mono font-bold tabular-nums text-neg bg-bg-0/85 border border-neg/60 shadow-[0_0_8px_rgb(var(--neg)/0.55)]"
                >
                  REC {recordingSeconds}s
                </span>
              </>
            )}
          </button>

          <button
            type="submit"
            disabled={isSending || !inputValue.trim()}
            aria-label={language === "de" ? "Senden" : "Send"}
            title={language === "de" ? "Senden" : "Send"}
            className={cn(
              "shrink-0 h-11 w-11 rounded-full",
              "flex items-center justify-center",
              "transition-all duration-150",
              "bg-bg-0/60 backdrop-blur-sm",
              !isSending && inputValue.trim()
                ? "text-[#00B8D9] border border-[#00B8D9]/70 shadow-[inset_0_0_0_1px_rgba(0,184,217,0.3),0_0_22px_-2px_rgba(0,184,217,0.55)] hover:border-[#00B8D9] active:scale-95"
                : "text-fg-subtle border border-fg-subtle/30",
              "disabled:opacity-40 disabled:cursor-not-allowed",
            )}
          >
            {isSending ? (
              <Loader2 className="h-[18px] w-[18px] animate-spin" aria-hidden="true" />
            ) : (
              <Send className="h-[17px] w-[17px] -ml-0.5" aria-hidden="true" strokeWidth={2.25} />
            )}
          </button>
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
