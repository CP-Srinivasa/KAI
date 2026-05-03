// KAI Persona — Asset Mapper
// Spec source: docs/kai_persona/technical_ui_pack_v3_2.md §7
// DALI-Audit 2026-05-03: 7 separate state-images are NOT a Phase-1 prerequisite. Master-image +
// CSS-Layer (Glow, Border-Pulse, Overlay-Tint, Status-Pill) carry state differentiation in V1.
// State-specific assets layer on top as they become available, with explicit fallback chain.

import type { KaiAssetEntry, KaiState, KaiStateAssetSet, KaiAssetManifest } from "./types";

export interface KaiAssetSet {
  staticImage: string;
  animationGif: string | null;
  animationWebm: string | null;
  icon: string;
  isFromMasterFallback: boolean;
  fallbackReason?: string;
}

// Bundled at build-time so the SPA never hits a 404 race for the master.
// Anchor decided 2026-05-03: kai_master_v1.png is identity, NOT just a placeholder.
const MASTER_PORTRAIT_PATH = "/assets/kai/master/kai_master_v1.png";

const STATIC_PATHS: Record<KaiState, string> = {
  IDLE: "/assets/kai/states/kai_idle.png",
  ANALYSIS: "/assets/kai/states/kai_analysis.png",
  SIGNAL: "/assets/kai/states/kai_signal.png",
  WARNING: "/assets/kai/states/kai_warning.png",
  SECURITY: "/assets/kai/states/kai_security.png",
  ERROR: "/assets/kai/states/kai_error.png",
  OFFLINE: "/assets/kai/states/kai_offline.png",
};

const GIF_PATHS: Record<KaiState, string> = {
  IDLE: "/assets/kai/motion/gif/kai_idle_loop.gif",
  ANALYSIS: "/assets/kai/motion/gif/kai_analysis_loop.gif",
  SIGNAL: "/assets/kai/motion/gif/kai_signal_found.gif",
  WARNING: "/assets/kai/motion/gif/kai_risk_detected.gif",
  SECURITY: "/assets/kai/motion/gif/kai_security_scan.gif",
  ERROR: "/assets/kai/motion/gif/kai_error_detected.gif",
  OFFLINE: "/assets/kai/motion/gif/kai_no_signal.gif",
};

const WEBM_PATHS: Record<KaiState, string> = {
  IDLE: "/assets/kai/motion/webm/kai_idle_loop.webm",
  ANALYSIS: "/assets/kai/motion/webm/kai_analysis_loop.webm",
  SIGNAL: "/assets/kai/motion/webm/kai_signal_found.webm",
  WARNING: "/assets/kai/motion/webm/kai_risk_detected.webm",
  SECURITY: "/assets/kai/motion/webm/kai_security_scan.webm",
  ERROR: "/assets/kai/motion/webm/kai_error_detected.webm",
  OFFLINE: "/assets/kai/motion/webm/kai_no_signal.webm",
};

const ICON_NAMES: Record<KaiState, string> = {
  IDLE: "kai_idle",
  ANALYSIS: "kai_analysis",
  SIGNAL: "kai_signal",
  WARNING: "kai_warning",
  SECURITY: "kai_security",
  ERROR: "kai_error",
  OFFLINE: "kai_offline",
};

// Runtime-overridable: when the manifest reports an asset as 'placeholder', the resolver
// returns the master fallback so the UI never renders a broken image.
let _manifest: KaiAssetManifest | null = null;

export function setKaiAssetManifest(manifest: KaiAssetManifest): void {
  _manifest = manifest;
}

export function getMasterPortraitPath(): string {
  return _manifest?.assets.master_portrait_v1.path
    ? `${_manifest.asset_root}${_manifest.assets.master_portrait_v1.path}`
    : MASTER_PORTRAIT_PATH;
}

function isAvailable(entry?: KaiAssetEntry): boolean {
  return entry?.status === "available";
}

function resolveStateAssets(state: KaiState): KaiStateAssetSet | undefined {
  return _manifest?.assets.states?.[state];
}

export function getKaiAssetSet(state: KaiState): KaiAssetSet {
  const stateAssets = resolveStateAssets(state);

  // Static: prefer state-specific if available, otherwise fall back to master portrait.
  let staticImage = STATIC_PATHS[state];
  let isFromMasterFallback = false;
  let fallbackReason: string | undefined;

  if (stateAssets && !isAvailable(stateAssets.static)) {
    staticImage = getMasterPortraitPath();
    isFromMasterFallback = true;
    fallbackReason = `state ${state} static asset is placeholder; using master portrait + CSS state-glow`;
  } else if (!stateAssets) {
    // No manifest loaded yet — use master so the UI still renders.
    staticImage = getMasterPortraitPath();
    isFromMasterFallback = true;
    fallbackReason = "asset manifest not loaded yet";
  }

  // Motion: only return paths if manifest explicitly says 'available'. Otherwise null,
  // and the component renders the static image (no broken video URL).
  const animationGif =
    stateAssets && isAvailable(stateAssets.motion_gif) ? GIF_PATHS[state] : null;
  const animationWebm =
    stateAssets && isAvailable(stateAssets.motion_webm) ? WEBM_PATHS[state] : null;

  return {
    staticImage,
    animationGif,
    animationWebm,
    icon: ICON_NAMES[state],
    isFromMasterFallback,
    fallbackReason,
  };
}

// Helper for components: "should I show motion at all on this device?".
// DALI Audit Risk 3: WebM auf Mobile aus, prefers-reduced-motion respektieren.
export interface MotionPolicy {
  allowMotion: boolean;
  reasonIfBlocked?: string;
}

export function getMotionPolicy(opts: {
  isMobile: boolean;
  prefersReducedMotion: boolean;
  effectiveConnectionType?: string;
  isIntersecting: boolean;
}): MotionPolicy {
  if (opts.prefersReducedMotion) {
    return { allowMotion: false, reasonIfBlocked: "user prefers reduced motion" };
  }
  if (opts.isMobile) {
    return { allowMotion: false, reasonIfBlocked: "mobile viewport — static only" };
  }
  if (opts.effectiveConnectionType && ["2g", "3g", "slow-2g"].includes(opts.effectiveConnectionType)) {
    return { allowMotion: false, reasonIfBlocked: `slow connection (${opts.effectiveConnectionType})` };
  }
  if (!opts.isIntersecting) {
    return { allowMotion: false, reasonIfBlocked: "widget not in viewport" };
  }
  return { allowMotion: true };
}
