// KAI Avatar — state-driven portrait with CSS-glow + scanline + tint.
// DALI-Audit-Layer: 7 separate state-PNGs are NOT a Phase-1 prerequisite. Master-portrait
// + CSS-Layer carry state-differentiation. State-specific assets layer on top as they
// become available (Phase D Asset-Production).

import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { getKaiAssetSet, getMotionPolicy } from "../../kai/assetMapper";
import type { KaiState } from "../../kai/types";

type Size = "full" | "compact" | "mobile";
type Props = {
  state: KaiState;
  size?: Size;
  className?: string;
};

const SIZE_PX: Record<Size, number> = {
  full: 96,
  compact: 24,
  mobile: 48,
};

/**
 * Returns true once the element scrolls into the viewport for the first time.
 * Used to defer animation activation (DALI-Audit Risk 3 performance budget).
 */
function useInViewport<T extends HTMLElement>(): [React.RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);

  useEffect(() => {
    if (seen || !ref.current || typeof IntersectionObserver === "undefined") return;
    const node = ref.current;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          obs.disconnect();
        }
      },
      { threshold: 0.1 },
    );
    obs.observe(node);
    return () => obs.disconnect();
  }, [seen]);

  return [ref, seen];
}

function usePrefersReducedMotion(): boolean {
  const [prefers, setPrefers] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setPrefers(mq.matches);
    const handler = (e: MediaQueryListEvent) => setPrefers(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return prefers;
}

function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(max-width: 768px)");
    setIsMobile(mq.matches);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return isMobile;
}

function useEffectiveConnectionType(): string | undefined {
  const [type, setType] = useState<string | undefined>(undefined);
  useEffect(() => {
    if (typeof navigator === "undefined") return;
    // navigator.connection is non-standard; type-narrow via record.
    const conn = (navigator as unknown as { connection?: { effectiveType?: string } }).connection;
    if (conn?.effectiveType) setType(conn.effectiveType);
  }, []);
  return type;
}

function KaiAvatarVideo({ src, poster, playLimit }: { src: string; poster: string; playLimit: number }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const loopsCompletedRef = useRef(0);
  const lastTimeRef = useRef(0);
  const stoppedRef = useRef(false);
  return (
    <video
      ref={videoRef}
      src={src}
      poster={poster}
      autoPlay
      muted
      loop
      playsInline
      controls={false}
      controlsList="nodownload nofullscreen noremoteplayback noplaybackrate"
      disablePictureInPicture
      disableRemotePlayback
      className="kai-avatar-video"
      onTimeUpdate={() => {
        const v = videoRef.current;
        if (!v || stoppedRef.current) return;
        // Wraparound-Detection: currentTime springt von ~duration auf ~0 zurück
        if (v.currentTime + 0.5 < lastTimeRef.current) {
          loopsCompletedRef.current += 1;
          if (loopsCompletedRef.current >= playLimit) {
            stoppedRef.current = true;
            v.loop = false;
            v.pause();
            // Auf letzten Frame springen (= poster), kein Controls-Flash
            if (Number.isFinite(v.duration)) {
              v.currentTime = Math.max(0, v.duration - 0.05);
            }
          }
        }
        lastTimeRef.current = v.currentTime;
      }}
    />
  );
}

export function KaiAvatar({ state, size = "full", className }: Props) {
  const [ref, isIntersecting] = useInViewport<HTMLDivElement>();
  const prefersReducedMotion = usePrefersReducedMotion();
  const isMobile = useIsMobile();
  const ect = useEffectiveConnectionType();

  const assets = useMemo(() => getKaiAssetSet(state), [state]);

  const motion = useMemo(
    () =>
      getMotionPolicy({
        isMobile,
        prefersReducedMotion,
        effectiveConnectionType: ect,
        isIntersecting,
      }),
    [isMobile, prefersReducedMotion, ect, isIntersecting],
  );

  const px = SIZE_PX[size];

  return (
    <div
      ref={ref}
      className={cn("kai-avatar", `kai-avatar--${state}`, className)}
      style={{ width: px, height: px }}
      aria-label={`KAI avatar in state ${state}`}
    >
      <div className="kai-avatar-shell" style={{ width: px, height: px }}>
        {motion.allowMotion && assets.animationWebm ? (
          <KaiAvatarVideo
            src={assets.animationWebm}
            poster={assets.staticImage}
            playLimit={2}
          />
        ) : (
          <img
            src={assets.staticImage}
            alt={`KAI ${state}`}
            className="kai-avatar-img"
            loading={size === "compact" ? "eager" : "lazy"}
            decoding="async"
          />
        )}
      </div>
    </div>
  );
}
