// @data-source: props (POST /pay/requests → lightning_uri)
import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { qrPayload } from "@/lib/pay";

// KAI PAY v0.1 — QR-Code aus der Lightning-URI. Rendert als DataURL (<img>),
// damit der Code auch ohne Canvas-Zugriff im Test sichtbar ist. Payload in
// GROSSBUCHSTABEN (lib/pay.ts::qrPayload). Weißer Hintergrund bleibt auch im
// Dark-Theme — Scanner brauchen Kontrast, nicht Theme-Treue.

export function PayQr({ lightningUri, size = 224 }: { lightningUri: string; size?: number }) {
  const payload = qrPayload(lightningUri);
  const [dataUrl, setDataUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setDataUrl(null);
    setError(null);
    QRCode.toDataURL(payload, { errorCorrectionLevel: "M", margin: 2, width: size })
      .then((url) => {
        if (alive) setDataUrl(url);
      })
      .catch((e: unknown) => {
        if (alive) setError((e as Error)?.message || "QR-Rendering fehlgeschlagen");
      });
    return () => {
      alive = false;
    };
  }, [payload, size]);

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-md border border-neg/30 bg-neg/10 px-3 py-2 text-xs text-neg"
        style={{ width: size }}
      >
        QR konnte nicht gerendert werden: {error}
      </div>
    );
  }
  if (!dataUrl) {
    return (
      <div
        role="status"
        aria-busy="true"
        className="grid place-items-center rounded-md border border-line-subtle bg-bg-2 text-2xs text-fg-subtle"
        style={{ width: size, height: size }}
      >
        QR …
      </div>
    );
  }
  return (
    <img
      src={dataUrl}
      width={size}
      height={size}
      alt="Lightning-Invoice als QR-Code"
      data-testid="pay-qr"
      className="rounded-md bg-white p-2 shadow-panel"
    />
  );
}
