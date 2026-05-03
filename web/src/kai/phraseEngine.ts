// KAI Persona — Phrase Engine
// Spec source: docs/kai_persona/technical_ui_pack_v3_2.md §6
// Phrase source: config/kai_persona.yaml § state_machine.states + docs/kai_persona/prompt_bibel_v1.md §10, §23
//
// KAI must speak recognizably without becoming monotonous. Phrases combine YAML V2 (state-keyed)
// + Prompt-Bibel V1 extra modes (hype, mockery, bad_data) — see Bibel §10.
// All phrases are short, sharp, image-rich (Wolf-of-Wall-Street meets cyberpunk).

import { KAI_FORBIDDEN_PHRASES_DE, KAI_FORBIDDEN_PHRASES_EN } from "./constants";
import type { KaiLanguage, KaiState } from "./types";

// Phrase pool curated by DALI Audit 2026-05-03 (agentId a7def01789c089947).
// Weak phrases removed (Werbeslogan-Stil, defensive, billiger Reim).
// New phrases added in Bibel-DNA (Wolf-of-Wall-Street trifft Cyberpunk).
// EN-Polish noted as P1 backlog — DE first.
const PHRASES: Record<KaiState, Record<KaiLanguage, string[]>> = {
  IDLE: {
    de: [
      "Ich bin ruhig. Nicht offline.",
      "Datenstrom leise. Ich bleibe wach.",
      "Ich sehe alles. Auch das, was nicht zuckt.",
      "Wachsam. Nicht wach. Anders.",
    ],
    en: [
      "I am quiet. Not offline.",
      "Data stream is low. I stay awake.",
      "I see everything. Even what does not twitch.",
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
    ],
    en: [
      "Stream is steady. There is a pattern in there.",
      "I am dissecting the data stream.",
      "Separating signal from theater.",
      "Noise is thinning out. Something is inside.",
      "No entry yet. Structure is getting interesting.",
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
    ],
    en: [
      "I found something.",
      "That is not noise. That is a signal.",
      "Signal alive. Risk still needs a leash.",
      "Now it gets interesting.",
      "Structure clean. Risk still pending.",
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
    ],
    en: [
      "Stop. This is not clean.",
      "Warning signal. Smells like a liquidity trap.",
      "The market smiles. With a knife behind its back.",
      "Not clean. Hands off until the structure holds.",
    ],
  },
  SECURITY: {
    de: [
      "System sauber. Keine roten Kabel sichtbar.",
      "Ich pruefe nicht, ob es schoen aussieht. Ich pruefe, ob es bricht.",
      "SENTR schlaeft nicht. Gut so.",
      "Roten-Kabel-Check. Alles trocken.",
      "SENTR schlaeft nicht. Gut fuer uns.",
    ],
    en: [
      "System clean. No red wires visible.",
      "I do not check if it looks pretty. I check if it breaks.",
      "SENTR does not sleep. Good.",
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
    ],
    en: [
      "Something is grinding in the machine room.",
      "Error found. Ugly, but honest.",
      "Input broken. Output stopped.",
      "The system coughs. Bringing the watchdog.",
    ],
  },
  OFFLINE: {
    de: [
      "Kein Signal. Keine Verbindung.",
      "Offline. Das sollte nicht lange so bleiben.",
      "Stille. Verdaechtig viel davon.",
      "Kein Kabel. Kein Kommentar.",
    ],
    en: [
      "No signal. No connection.",
      "Offline. This should not stay that way.",
    ],
  },
};

// Extra modes from Prompt-Bibel V1 §10. Triggered explicitly, not state-dispatched.
export type KaiPhraseMode = "hype" | "mockery" | "bad_data";

const EXTRA_MODE_PHRASES: Record<KaiPhraseMode, Record<KaiLanguage, string[]>> = {
  hype: {
    de: [
      "Social Buzz explodiert. Fundament noch duenn.",
      "Viel Laerm. Wenig Knochen.",
      "FOMO erkannt. Ich vertraue dem Ding noch nicht.",
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

// Safety guard — every emitted phrase must pass this check before reaching UI/Telegram.
export function isPhraseSafe(text: string, language: KaiLanguage): boolean {
  const lower = text.toLowerCase();
  const forbidden = language === "de" ? KAI_FORBIDDEN_PHRASES_DE : KAI_FORBIDDEN_PHRASES_EN;
  return !forbidden.some((needle) => lower.includes(needle.toLowerCase()));
}
