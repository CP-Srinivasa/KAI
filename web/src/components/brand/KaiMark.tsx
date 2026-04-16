import type { SVGProps } from "react";

/** KAI monogram — bold geometric K with chevron pierce. Uses currentColor. */
export function KaiMark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 128 128"
      fill="currentColor"
      fillRule="evenodd"
      role="img"
      aria-label="KAI"
      {...props}
    >
      <title>KAI</title>
      <rect x="22" y="18" width="16" height="92" rx="2" />
      <path d="M38 64 L96 6 L110 22 L52 80 Z" />
      <path d="M38 64 L82 110 L66 110 L30 72 Z" />
    </svg>
  );
}

export function KaiWordmark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 320 96"
      fill="currentColor"
      fillRule="evenodd"
      role="img"
      aria-label="KAI"
      {...props}
    >
      <title>KAI</title>
      <rect x="14" y="10" width="14" height="76" rx="1.5" />
      <path d="M28 48 L78 4 L90 16 L40 60 Z" />
      <path d="M28 48 L70 90 L56 90 L22 56 Z" />
      <polygon points="128,90 142,90 166,18 152,18" />
      <polygon points="162,18 176,18 200,90 186,90" />
      <rect x="144" y="54" width="40" height="10" />
      <polygon points="152,18 176,18 164,6" />
      <rect x="222" y="10" width="14" height="76" rx="1.5" />
    </svg>
  );
}
