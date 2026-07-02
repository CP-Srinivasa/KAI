// KAI Persona — Public API barrel.
// Spec source: docs/kai_persona/technical_ui_pack_v3_2.md §1
// Motto: Persona non grata

export * from "./types";
export {
  KAI_STATE_PRIORITY,
  KAI_STATUS_LABEL,
  KAI_STATE_COLOR,
  KAI_STATE_ICON,
  KAI_STATE_ANIMATION,
  KAI_FORBIDDEN_PHRASES_DE,
  KAI_FORBIDDEN_PHRASES_EN,
  KAI_DEFAULT_LANGUAGE,
  KAI_BRAND_MOTTO,
  KAI_BRAND_NAME,
  KAI_BRAND_FULL_NAME,
} from "./constants";
export { resolveKaiState, createFallbackState, failClosedState, isValidKaiState } from "./stateResolver";
export { getKaiPhrase, getKaiExtraModePhrase, isPhraseSafe } from "./phraseEngine";
export type { KaiPhraseMode } from "./phraseEngine";
export { validateSignalForLivetrade, validateSignalInvariants } from "./riskGuards";
export type { KaiGuardResult } from "./riskGuards";
export {
  getKaiAssetSet,
  setKaiAssetManifest,
  getMasterPortraitPath,
  getMotionPolicy,
} from "./assetMapper";
export type { KaiAssetSet, MotionPolicy } from "./assetMapper";
export {
  buildKaiStateChangedEvent,
  buildKaiSignalRenderedEvent,
  buildKaiWarningRenderedEvent,
  buildKaiSecurityReportEvent,
  buildKaiLivetradeBlockedEvent,
  buildKaiAgentSummaryEvent,
  buildKaiAssetFallbackEvent,
  buildKaiConfigValidationFailedEvent,
  buildKaiAuditEvent,
} from "./auditMapper";
export { loadKaiPersona, getCachedPersona, resetKaiPersonaCache } from "./personaLoader";
export type { KaiPersonaSnapshot } from "./personaLoader";
