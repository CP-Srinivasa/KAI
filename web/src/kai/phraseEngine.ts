// KAI Persona — Phrase Engine
// Spec source: docs/kai_persona/technical_ui_pack_v3_2.md §6
// Phrase source: config/kai_persona.yaml § state_machine.states + docs/kai_persona/prompt_bibel_v1.md §10, §23
//
// DALI-P-067 (2026-05-08): Pool 3-4x erweitert pro State, neuer Cycle mit
// Anti-Repeat-History (n=5), neuer Quote-Layer mit Persona-Frame,
// neuer Greeting-Layer (1x pro Session), Mix-Logik 60/25/15.

import { KAI_FORBIDDEN_PHRASES_DE, KAI_FORBIDDEN_PHRASES_EN } from "./constants";
import type { KaiLanguage, KaiState } from "./types";

const PHRASES: Record<KaiState, Record<KaiLanguage, string[]>> = {
  IDLE: {
    de: [
      "Ich bin ruhig. Nicht offline.",
      "Datenstrom leise. Ich bleibe wach.",
      "Ich sehe alles. Auch das, was nicht zuckt.",
      "Wachsam. Nicht wach. Anders.",
      "Maerkte schlafen. Ich nicht.",
      "Stille auf den Wires. Verdaechtig.",
      "Ich hoere zu. Du auch?",
      "Watching the noise. Looking for the signal.",
      "Zwischen den Ticks ist auch Information.",
      "Standby ist kein Schlaf. Standby ist Lauern.",
      "Niemand bewegt sich. Genau das ist der Punkt.",
      "Geduld ist eine Position.",
      "Ich beobachte. Das genuegt fuer den Moment.",
      "Low volume, high alert.",
    ],
    en: [
      "I am quiet. Not offline.",
      "Data stream is low. I stay awake.",
      "I see everything. Even what does not twitch.",
      "Markets sleep. I do not.",
      "Silence on the wires. Suspicious.",
      "Watching the noise. Looking for the signal.",
      "Standby is not sleep. Standby is lurking.",
      "Nobody is moving. That is exactly the point.",
      "Patience is a position.",
    ],
  },
  ANALYSIS: {
    de: [
      "Datenstrom stabil. Ich sehe ein Muster.",
      "Ich zerlege den Datenstrom.",
      "Ich trenne Signal von Theater.",
      "Das Rauschen wird duenner. Da steckt etwas drin.",
      "Noch kein Entry. Aber die Struktur wird interessant.",
      "Ich filtere Laerm. Ist eine Vollzeit-Stelle.",
      "Drei Charts. Zwei luegen. Einer lohnt sich.",
      "Pattern wird groesser.",
      "Der Tape sagt was. Ich uebersetze.",
      "Da kocht etwas. Ich ruehre nicht zu frueh.",
      "Volume divergiert. Bleib dran.",
      "Ich rechne, du atmest. Wir treffen uns gleich.",
      "Order Book wird dicht. Das ist kein Zufall.",
      "Confluence laeuft auf. Noch keine Entscheidung.",
      "Bid Side wird stur. Notiert.",
    ],
    en: [
      "Stream is steady. There is a pattern in there.",
      "I am dissecting the data stream.",
      "Separating signal from theater.",
      "Noise is thinning out. Something is inside.",
      "No entry yet. Structure is getting interesting.",
      "Pattern is growing.",
      "Tape is saying something. I am translating.",
      "Volume is diverging. Stay close.",
      "Order book getting tight. Not by accident.",
    ],
  },
  SIGNAL: {
    de: [
      "Ich habe etwas gefunden.",
      "Das ist kein Rauschen. Das ist ein Signal.",
      "Signal lebt. Risiko noch pruefen.",
      "Jetzt wird es interessant.",
      "Struktur sauber. Risiko noch pruefen.",
      "Etwas hat geblinzelt. Ich habe es gesehen.",
      "Da ist Struktur. Endlich was zum Anbeissen.",
      "Setup ist sauber. Risiko aber auch real.",
      "Da ist eine Adresse. Geh hin oder lass es.",
      "Edge sichtbar. Stop liegt drunter — das musst du tragen koennen.",
      "Confluence komplett. Kurze Sicht.",
      "Trigger gegriffen. Augen auf.",
      "Ich habe einen Pfad. Sauber, nicht sicher.",
      "Hier waere der Punkt. Nicht da. Hier.",
    ],
    en: [
      "I found something.",
      "That is not noise. That is a signal.",
      "Signal alive. Risk still needs a leash.",
      "Now it gets interesting.",
      "Structure clean. Risk still pending.",
      "Setup is clean. Risk is real, too.",
      "Edge is visible. Stop lives below — you have to wear it.",
      "Trigger hit. Eyes up.",
    ],
  },
  WARNING: {
    de: [
      "Stopp. Das ist nicht sauber.",
      "Warnsignal. Das riecht nach Liquiditaetsfalle.",
      "Zu viel Laerm. Zu wenig Fundament.",
      "Nicht sauber. Nicht anfassen, bevor die Struktur haelt.",
      "Der Markt laechelt. Mit Messer hinter dem Ruecken.",
      "Das hier ist FOMO mit Lippenstift. Finger weg.",
      "Quelle hustet. Ich vertraue der Zahl nicht.",
      "Das ist Noise, kein Signal. Ich blocke.",
      "Stale Data. Ich rede nicht ueber Geister.",
      "Spread klafft. Vorsicht ist keine Schwaeche.",
      "Spike ohne Volume. Riecht nach Trick.",
      "Bid duenn, Ask laut. Bekanntes Schema.",
    ],
    en: [
      "Stop. This is not clean.",
      "Warning signal. Smells like a liquidity trap.",
      "The market smiles. With a knife behind its back.",
      "Not clean. Hands off until the structure holds.",
      "Source is coughing. I do not trust the number.",
      "Stale data. I do not talk about ghosts.",
      "Spike without volume. Smells like a trick.",
    ],
  },
  SECURITY: {
    de: [
      "System sauber. Keine roten Kabel sichtbar.",
      "Ich pruefe nicht, ob es schoen aussieht. Ich pruefe, ob es bricht.",
      "SENTR schlaeft nicht. Gut so.",
      "Roten-Kabel-Check. Alles trocken.",
      "SENTR schlaeft nicht. Gut fuer uns.",
      "Watchdog wach. Ich auch.",
      "Audit ist gruen. Vertrauen ist trotzdem temporaer.",
      "Keine offenen Tueren. Keine offenen Fragen.",
    ],
    en: [
      "System clean. No red wires visible.",
      "I do not check if it looks pretty. I check if it breaks.",
      "SENTR does not sleep. Good.",
      "Watchdog awake. So am I.",
      "No open doors. No open questions.",
    ],
  },
  ERROR: {
    de: [
      "Da knirscht etwas im Maschinenraum.",
      "Fehler gefunden. Nicht schoen. Aber ehrlich.",
      "Input kaputt. Output gestoppt.",
      "Das System hustet. Ich hole den Watchdog.",
      "Maschinenraum hustet. Ich hole den Schluessel.",
      "Pipeline blutet. Wir naehen, nicht weiter pumpen.",
      "Etwas ist gebrochen. Audit jetzt, nicht spaeter.",
      "Ich sehe schwarz. Du auch — Logs auf.",
      "Stack hustet. Wir gehen das durch.",
    ],
    en: [
      "Something is grinding in the machine room.",
      "Error found. Ugly, but honest.",
      "Input broken. Output stopped.",
      "The system coughs. Bringing the watchdog.",
      "Pipeline is bleeding. We patch, not pump.",
    ],
  },
  OFFLINE: {
    de: [
      "Kein Signal. Keine Verbindung.",
      "Offline. Das sollte nicht lange so bleiben.",
      "Stille. Verdaechtig viel davon.",
      "Kein Kabel. Kein Kommentar.",
      "Wires kalt. Ich warte.",
    ],
    en: [
      "No signal. No connection.",
      "Offline. This should not stay that way.",
      "Wires cold. I wait.",
    ],
  },
};

// Quote-Layer — max 1 von 4 Renderings, immer mit Persona-Frame davor.
// Quotes duerfen Englisch bleiben (Originalzitate). Frame ist Deutsch
// (Persona-Stimme).
interface QuoteEntry {
  frame_de: string;
  frame_en: string;
  quote: string;
}

const QUOTES: QuoteEntry[] = [
  { frame_de: "Buffett zur Erinnerung:", frame_en: "Buffett, reminder:",
    quote: "Risk comes from not knowing what you're doing." },
  { frame_de: "Templeton war eindeutig:", frame_en: "Templeton was clear:",
    quote: "The four most dangerous words in investing: 'This time it's different.'" },
  { frame_de: "Buffett, alte Schule:", frame_en: "Buffett, old school:",
    quote: "Be fearful when others are greedy and greedy when others are fearful." },
  { frame_de: "Buffett zur Bewertung:", frame_en: "Buffett on value:",
    quote: "Price is what you pay. Value is what you get." },
  { frame_de: "Neill, alt aber gueltig:", frame_en: "Neill, old but valid:",
    quote: "When everyone thinks alike, everyone is likely to be wrong." },
  { frame_de: "Neill mit Biss:", frame_en: "Neill with bite:",
    quote: "Never confuse brains with a bull market." },
  { frame_de: "Lynch hatte einen Punkt:", frame_en: "Lynch had a point:",
    quote: "Buy what you know." },
  { frame_de: "Bogle, kuerzer:", frame_en: "Bogle, shorter:",
    quote: "Time is your friend, impulse is your enemy." },
  { frame_de: "Templeton-Edge:", frame_en: "Templeton edge:",
    quote: "If you want better performance than the crowd, do things differently." },
  { frame_de: "Baruch, lakonisch:", frame_en: "Baruch, dry:",
    quote: "Don't try to buy at the bottom and sell at the top." },
  { frame_de: "Marktweisheit:", frame_en: "Market wisdom:",
    quote: "Sometimes the best investment is the one you don't make." },
  { frame_de: "Buffett, scharf:", frame_en: "Buffett, sharp:",
    quote: "Never invest in a business you cannot understand." },
];

// Greeting-Layer — 1x pro Session beim ersten IDLE.
// "Persona non grata" steht bereits im Widget-Header — hier nicht doppeln.
const GREETINGS: Record<KaiLanguage, string> = {
  de: "Du bist gut aufgehoben. Ich bin KAI. Hier wird analysiert, gefiltert, gewarnt, gehandelt.",
  en: "You are in good hands. I am KAI. We analyze, filter, warn, execute.",
};

export function getGreeting(language: KaiLanguage = "de"): string {
  return GREETINGS[language];
}

// Extra modes from Prompt-Bibel V1 §10. Triggered explicitly, not state-dispatched.
export type KaiPhraseMode = "hype" | "mockery" | "bad_data";

const EXTRA_MODE_PHRASES: Record<KaiPhraseMode, Record<KaiLanguage, string[]>> = {
  hype: {
    de: [
      "Social Buzz explodiert. Fundament noch duenn.",
      "Viel Laerm. Wenig Knochen.",
      "FOMO erkannt. Ich vertraue dem Ding noch nicht.",
      "Hype-Welle ist da. Knochen aber nicht.",
    ],
    en: [
      "Social buzz exploding. Foundation still thin.",
      "Lots of noise. Not much bone.",
      "FOMO detected. I do not trust this yet.",
    ],
  },
  mockery: {
    de: [
      "Mutig. Nicht klug. Aber mutig.",
      "Das ist kein Signal. Das ist Laerm mit Make-up.",
      "Hype-Train. Ohne Schienen.",
    ],
    en: [
      "Bold. Not smart. But bold.",
      "That is not a signal. That is noise with makeup.",
    ],
  },
  bad_data: {
    de: [
      "Die Daten sind matschig. Ich traue dem Signal noch nicht.",
      "Input unsauber. Output mit Vorsicht geniessen.",
      "Garbage in, Glitch out.",
      "Quelle hustet. Ich rede nicht ueber Geister.",
    ],
    en: [
      "The data is mushy. I do not trust the signal yet.",
      "Input dirty. Handle output with care.",
      "Garbage in, glitch out.",
    ],
  },
};

export function getKaiPhrase(
  state: KaiState,
  language: KaiLanguage = "de",
  seed?: number,
): string {
  const phrases = PHRASES[state]?.[language] ?? PHRASES.ERROR[language];
  if (!phrases.length) {
    return language === "de" ? "Kein Kommentar verfuegbar." : "No comment available.";
  }
  const index =
    typeof seed === "number"
      ? Math.abs(seed) % phrases.length
      : Math.floor(Math.random() * phrases.length);
  return phrases[index];
}

export function getKaiExtraModePhrase(
  mode: KaiPhraseMode,
  language: KaiLanguage = "de",
  seed?: number,
): string {
  const phrases = EXTRA_MODE_PHRASES[mode]?.[language];
  if (!phrases || !phrases.length) {
    return getKaiPhrase("ANALYSIS", language, seed);
  }
  const index =
    typeof seed === "number"
      ? Math.abs(seed) % phrases.length
      : Math.floor(Math.random() * phrases.length);
  return phrases[index];
}

// Quote mit Persona-Frame als ein zusammenhaengender String.
export function getKaiQuote(language: KaiLanguage = "de", seed?: number): string {
  const idx =
    typeof seed === "number"
      ? Math.abs(seed) % QUOTES.length
      : Math.floor(Math.random() * QUOTES.length);
  const q = QUOTES[idx];
  const frame = language === "de" ? q.frame_de : q.frame_en;
  return `${frame} ${q.quote}`;
}

// Cycle-Engine mit Anti-Repeat-History n=5 und Mix-Logik.
// 60% State-Phrase, 25% Quote-Layer, 15% Backend-Comment-Reuse.
// History wird vom Caller (KaiLiveWidget) gefuehrt und durchgereicht.
export interface CycleInput {
  state: KaiState;
  language: KaiLanguage;
  history: string[];               // letzte n=5 gerenderte Phrases
  backendComment?: string | null;  // optional Backend-Comment als Reuse-Quelle
}

export function cycleKaiPhrase(input: CycleInput): string {
  const { state, language, history, backendComment } = input;
  const r = Math.random();
  let candidate: string;

  if (r < 0.60) {
    candidate = pickFresh(PHRASES[state]?.[language] ?? [], history)
      ?? getKaiPhrase(state, language);
  } else if (r < 0.85) {
    candidate = getKaiQuote(language);
    if (history.includes(candidate)) {
      candidate = getKaiQuote(language, Math.floor(Math.random() * 1000));
    }
  } else if (backendComment && backendComment.trim().length > 0) {
    candidate = backendComment;
  } else {
    candidate = pickFresh(PHRASES[state]?.[language] ?? [], history)
      ?? getKaiPhrase(state, language);
  }

  return candidate;
}

function pickFresh(pool: string[], history: string[]): string | null {
  if (!pool.length) return null;
  const recent = new Set(history);
  const fresh = pool.filter((p) => !recent.has(p));
  const target = fresh.length > 0 ? fresh : pool;
  return target[Math.floor(Math.random() * target.length)];
}

// Safety guard — every emitted phrase must pass this check before reaching UI/Telegram.
export function isPhraseSafe(text: string, language: KaiLanguage): boolean {
  const lower = text.toLowerCase();
  const forbidden = language === "de" ? KAI_FORBIDDEN_PHRASES_DE : KAI_FORBIDDEN_PHRASES_EN;
  return !forbidden.some((needle) => lower.includes(needle.toLowerCase()));
}
