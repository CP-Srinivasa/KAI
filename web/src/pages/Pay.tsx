// @data-source: /pay/health + POST /pay/requests + /pay/requests/{id} (+ /receipt) + /pay/requests?limit=10
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { AlertTriangle, Check, Copy, QrCode, Receipt, RefreshCw } from "lucide-react";
import { PageHeader } from "@/layout/PageHeader";
import { Badge, Button, Card, CardHeader } from "@/components/ui/Primitives";
import { Field, Input } from "@/components/ui/Form";
import { EmptyState } from "@/components/ui/EmptyState";
import { PayQr } from "@/components/panels/PayQr";
import { useApi, type AsyncState } from "@/lib/useApi";
import { PAY_POLL_MS, usePayRequestPolling } from "@/lib/usePayRequestPolling";
import {
  createPayRequest,
  fetchPayHealth,
  fetchPayReceipt,
  fetchPayRequests,
  type PayHealth,
  type PayReceipt,
  type PayRequest,
  type PayRequestCreated,
} from "@/lib/api";
import {
  PAY_DESCRIPTION_MAX,
  PAY_REFERENCE_MAX,
  describeError,
  formatCountdown,
  formatSats,
  isPayDisabledError,
  normalizePayList,
  payStatusLabel,
  payStatusTone,
  remainingSeconds,
  toPayRequest,
  validatePayForm,
  type PayFormErrors,
} from "@/lib/pay";
import { formatDayTime } from "@/lib/time";
import { cn } from "@/lib/utils";

// KAI PAY v0.1 — EINE funktionale Seite (Operator-Vorgabe):
//
//   KAI PAY
//   Amount:      [ 5000 ]   (sat, Ganzzahl — keine EUR-Anzeige)
//   Description: [ Test ]
//   [ CREATE PAYMENT ]
//             QR
//   Status: WAITING          → nach Zahlung: ✓ PAYMENT SETTLED
//
// Wahrheitsregeln: Status kommt NUR aus `GET /pay/requests/{id}` (Polling alle
// 3 s bis Endzustand), Fehler der API stehen immer lesbar auf der Seite, und
// ein deaktiviertes Feature (`/pay/health` → 404) zeigt einen Hinweis statt
// eines toten Formulars. bolt11/QR gibt es nur aus der POST-Antwort — der
// Vertrag liefert sie im GET nicht, also merkt sich die Seite Invoices der
// laufenden Sitzung und behauptet für ältere Requests keinen QR.

type Invoice = { bolt11: string; lightning_uri: string };
type Selection = { paymentId: string; seed: PayRequest | null };

const LIST_LIMIT = 10;
const LIST_REFRESH_MS = 10_000;
const HEALTH_REFRESH_MS = 30_000;

export function PayPage({ pollMs = PAY_POLL_MS }: { pollMs?: number } = {}) {
  const health = useApi(fetchPayHealth, HEALTH_REFRESH_MS);
  const disabled =
    (health.state === "error" && isPayDisabledError(health.error)) ||
    (health.state === "ready" && health.data.enabled === false);

  // Liste neu laden über einen Dep-Tick statt `list.reload()`: useApi liefert
  // vor dem ersten erfolgreichen Laden nur ein No-op-reload — ein Create in
  // dieser Lücke würde die Liste sonst still nicht aktualisieren.
  const [listTick, setListTick] = useState(0);
  const bumpList = useCallback(() => setListTick((t) => t + 1), []);
  const list = useApi(
    (s) => fetchPayRequests(LIST_LIMIT, s).then(normalizePayList),
    LIST_REFRESH_MS,
    [listTick],
  );

  const [selection, setSelection] = useState<Selection | null>(null);
  // Invoices (bolt11 + lightning_uri) dieser Sitzung, Schlüssel payment_id.
  const invoicesRef = useRef<Map<string, Invoice>>(new Map());

  const onCreated = useCallback((created: PayRequestCreated) => {
    invoicesRef.current.set(created.payment_id, {
      bolt11: created.bolt11,
      lightning_uri: created.lightning_uri,
    });
    setSelection({ paymentId: created.payment_id, seed: toPayRequest(created) });
    bumpList();
  }, [bumpList]);

  const onSelect = useCallback((req: PayRequest) => {
    setSelection({ paymentId: req.payment_id, seed: req });
  }, []);

  const onTerminal = useCallback(() => {
    bumpList();
  }, [bumpList]);

  if (disabled) {
    return <PayDisabled detail={health.state === "error" ? health.error.message : "enabled=false"} />;
  }

  return (
    <div className="p-5 xl:p-6 space-y-6 max-w-[1680px] mx-auto">
      <PageHeader
        title="KAI PAY"
        sub="Zahlung anfordern · Lightning-Invoice · Status live aus der API"
        tone="pos"
        icon={<QrCode size={18} />}
        right={<HealthBadge health={health} />}
      />

      {health.state === "error" && (
        <ErrorLine
          label="/pay/health nicht erreichbar"
          message={`${health.error.kind} · ${health.error.message}`}
          onRetry={health.reload}
        />
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <PayForm onCreated={onCreated} />
        {selection ? (
          <PayRequestView
            key={selection.paymentId}
            paymentId={selection.paymentId}
            seed={selection.seed}
            invoice={invoicesRef.current.get(selection.paymentId) ?? null}
            pollMs={pollMs}
            onTerminal={onTerminal}
          />
        ) : (
          <Card padded>
            <EmptyState
              icon={<QrCode size={18} />}
              title="Noch keine Zahlung angefordert"
              hint="Betrag und Beschreibung eingeben, dann CREATE PAYMENT — QR-Code und Status erscheinen hier."
            />
          </Card>
        )}
      </div>

      <RecentRequests
        list={list}
        activeId={selection?.paymentId ?? null}
        onSelect={onSelect}
        onReload={bumpList}
      />
    </div>
  );
}

/* ---------- Deaktiviert (404 auf /pay/health oder enabled=false) ---------- */

function PayDisabled({ detail }: { detail: string }) {
  return (
    <div className="p-5 xl:p-6 space-y-6 max-w-[1680px] mx-auto">
      <PageHeader
        title="KAI PAY"
        sub="Zahlung anfordern über Lightning"
        tone="warn"
        icon={<QrCode size={18} />}
      />
      <EmptyState
        icon={<AlertTriangle size={18} />}
        title="KAI PAY ist auf diesem Server nicht aktiviert (APP_PAY_ENABLED)"
        hint={
          <>
            Der Server antwortet auf <span className="font-mono">GET /pay/health</span> mit{" "}
            <span className="font-mono">{detail}</span>. Aktivieren:{" "}
            <span className="font-mono">APP_PAY_ENABLED=true</span> in der Server-
            <span className="font-mono">.env</span>, dann <span className="font-mono">kai-server</span>{" "}
            neu starten.
          </>
        }
      />
    </div>
  );
}

/* ---------- Health-Badge im Header ---------- */

function HealthBadge({ health }: { health: AsyncState<PayHealth> }) {
  if (health.state !== "ready") return null;
  const h = health.data;
  return (
    <Badge tone={h.poller_alive ? "pos" : "warn"} dot title="GET /pay/health">
      offen {h.open_requests} · settled {h.settled_total} · poller{" "}
      {h.poller_alive ? "alive" : "DOWN"}
    </Badge>
  );
}

/* ---------- Formular ---------- */

function PayForm({ onCreated }: { onCreated: (created: PayRequestCreated) => void }) {
  const [amountSat, setAmountSat] = useState("");
  const [description, setDescription] = useState("");
  const [reference, setReference] = useState("");
  const [errors, setErrors] = useState<PayFormErrors>({});
  const [apiError, setApiError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const v = validatePayForm({ amountSat, description, reference });
    if (!v.ok) {
      setErrors(v.errors);
      return;
    }
    setErrors({});
    setApiError(null);
    setBusy(true);
    try {
      const created = await createPayRequest(v.body);
      onCreated(created);
    } catch (err) {
      setApiError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card padded>
      <CardHeader
        title="Zahlung anfordern"
        subtitle="Betrag in sat (Ganzzahl) · Beschreibung · optionale Referenz"
      />
      <form onSubmit={submit} className="space-y-3" noValidate>
        <Field label="Amount (sat)" error={errors.amountSat} required>
          <Input
            name="amount_sat"
            inputMode="numeric"
            autoComplete="off"
            placeholder="1000"
            value={amountSat}
            onChange={(e) => setAmountSat(e.target.value)}
          />
        </Field>
        <Field label="Description" error={errors.description} required>
          <Input
            name="description"
            maxLength={PAY_DESCRIPTION_MAX}
            autoComplete="off"
            placeholder="Test"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        <Field label="Reference (optional)" error={errors.reference}>
          <Input
            name="reference"
            maxLength={PAY_REFERENCE_MAX}
            autoComplete="off"
            placeholder="z.B. Rechnungs-Nr."
            value={reference}
            onChange={(e) => setReference(e.target.value)}
          />
        </Field>
        {apiError && <ErrorLine label="Anfrage fehlgeschlagen" message={apiError} />}
        <Button
          type="submit"
          variant="primary"
          disabled={busy}
          className="w-full justify-center h-9 font-semibold tracking-wide"
        >
          {busy ? "… erstelle" : "CREATE PAYMENT"}
        </Button>
      </form>
    </Card>
  );
}

/* ---------- Ansicht eines Requests: QR · bolt11 · Countdown · Status · Receipt ---------- */

function useCountdown(expiresAt: string | null | undefined, active: boolean): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, [active, expiresAt]);
  return remainingSeconds(expiresAt, now);
}

type ReceiptState =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "ready"; data: PayReceipt }
  | { state: "error"; message: string };

function PayRequestView({
  paymentId,
  seed,
  invoice,
  pollMs,
  onTerminal,
}: {
  paymentId: string;
  seed: PayRequest | null;
  invoice: Invoice | null;
  pollMs: number;
  onTerminal: (req: PayRequest) => void;
}) {
  const poll = usePayRequestPolling(paymentId, seed, pollMs, onTerminal);
  const req = poll.request ?? seed;
  const status = req?.status ?? null;
  const remaining = useCountdown(req?.expires_at, status === "WAITING");
  const [receipt, setReceipt] = useState<ReceiptState>({ state: "idle" });

  const loadReceipt = async () => {
    setReceipt({ state: "loading" });
    try {
      setReceipt({ state: "ready", data: await fetchPayReceipt(paymentId) });
    } catch (e) {
      setReceipt({ state: "error", message: describeError(e) });
    }
  };

  return (
    <Card padded className="space-y-4">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <QrCode size={14} className="text-pos shrink-0" />
            Zahlung
            <span className="font-mono text-2xs text-fg-subtle break-all">{paymentId}</span>
          </span>
        }
        right={
          <Badge tone={payStatusTone(status)} dot>
            {payStatusLabel(status)}
          </Badge>
        }
      />

      <StatusLine req={req} remaining={remaining} />

      {invoice ? (
        <div className="flex flex-col items-center gap-3">
          <PayQr lightningUri={invoice.lightning_uri} />
          <CopyField label="bolt11" value={invoice.bolt11} />
        </div>
      ) : (
        <div className="rounded-sm border border-line-subtle bg-bg-2 px-3 py-2 text-2xs text-fg-muted leading-relaxed">
          Invoice (bolt11/QR) ist nur direkt nach dem Erstellen verfügbar —{" "}
          <span className="font-mono">GET /pay/requests/{"{id}"}</span> liefert sie nicht.
        </div>
      )}

      {req && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
          <Row k="Betrag">
            <span className="font-mono text-fg">{formatSats(req.amount_sat)}</span>
          </Row>
          <Row k="Beschreibung">{req.description || "—"}</Row>
          <Row k="Referenz">
            <span className="font-mono">{req.reference || "—"}</span>
          </Row>
          <Row k="Erstellt">
            <span className="font-mono">{formatDayTime(req.created_at)}</span>
          </Row>
          <Row k="Ablauf">
            <span className="font-mono">
              {formatDayTime(req.expires_at)}
              {status === "WAITING" && remaining != null && (
                <span className={cn("ml-2", remaining === 0 ? "text-neg" : "text-warn")}>
                  {remaining === 0 ? "erreicht" : `in ${formatCountdown(remaining)}`}
                </span>
              )}
            </span>
          </Row>
        </dl>
      )}

      {poll.error && <ErrorLine label="Status-Abfrage fehlgeschlagen" message={poll.error} />}
      {poll.polling && (
        <div className="text-2xs text-fg-subtle" aria-live="polite">
          Status wird alle {Math.round(pollMs / 100) / 10} s abgefragt …
        </div>
      )}

      {status === "SETTLED" && (
        <div className="space-y-2">
          {receipt.state !== "ready" && (
            <Button
              type="button"
              variant="outline"
              onClick={loadReceipt}
              disabled={receipt.state === "loading"}
            >
              <Receipt size={14} /> {receipt.state === "loading" ? "Receipt lädt …" : "Receipt"}
            </Button>
          )}
          {receipt.state === "error" && (
            <ErrorLine label="Receipt nicht ladbar" message={receipt.message} onRetry={loadReceipt} />
          )}
          {receipt.state === "ready" && <ReceiptView receipt={receipt.data} />}
        </div>
      )}
    </Card>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-fg-subtle">{k}</dt>
      <dd className="min-w-0 break-words text-fg">{children}</dd>
    </>
  );
}

function StatusLine({ req, remaining }: { req: PayRequest | null; remaining: number | null }) {
  if (!req) {
    return <div className="text-sm text-fg-muted">Status wird geladen …</div>;
  }
  switch (req.status) {
    case "WAITING":
      return (
        <div role="status" className="rounded-md border border-warn/30 bg-warn/5 px-4 py-3">
          <div className="text-lg font-mono font-semibold text-warn">Status: WAITING</div>
          <div className="mt-0.5 text-2xs text-fg-muted font-mono">
            {remaining == null
              ? "Ablaufzeit unbekannt"
              : remaining === 0
                ? "Ablaufzeit erreicht — warte auf Statuswechsel (EXPIRED)"
                : `Läuft ab in ${formatCountdown(remaining)}`}
          </div>
        </div>
      );
    case "SETTLED":
      return (
        <div role="status" className="rounded-md border border-pos/40 bg-pos/10 px-4 py-3">
          <div className="text-2xl font-bold tracking-wide text-pos">✓ PAYMENT SETTLED</div>
          <div className="mt-1 text-xs font-mono text-fg-muted">
            {formatSats(req.paid_amount_sat ?? req.amount_sat)} · {formatDayTime(req.paid_at)}
          </div>
        </div>
      );
    case "EXPIRED":
      return (
        <div role="status" className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3">
          <div className="text-2xl font-bold tracking-wide text-neg">✗ PAYMENT EXPIRED</div>
          <div className="mt-1 text-xs font-mono text-fg-muted">
            abgelaufen {formatDayTime(req.expires_at)} — neue Zahlung anfordern
          </div>
        </div>
      );
    case "FAILED":
      return (
        <div role="status" className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3">
          <div className="text-2xl font-bold tracking-wide text-neg">✗ PAYMENT FAILED</div>
          <div className="mt-1 text-xs font-mono text-fg-muted break-words">
            {req.last_error || "kein Fehlergrund vom Server"}
          </div>
        </div>
      );
    default:
      return (
        <div role="status" className="rounded-md border border-line bg-bg-2 px-4 py-3">
          <div className="text-lg font-mono font-semibold text-fg-muted">
            Status: {payStatusLabel(req.status)}
          </div>
          <div className="mt-0.5 text-2xs text-fg-muted">unbekannter Zustand vom Server</div>
        </div>
      );
  }
}

/* ---------- Receipt ---------- */

function ReceiptView({ receipt }: { receipt: PayReceipt }) {
  return (
    <div className="rounded-md border border-pos/25 bg-bg-2 px-3 py-2.5 space-y-2">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-fg">
        <Receipt size={12} className="text-pos" /> Receipt
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        <Row k="receipt_id">
          <span className="font-mono">{receipt.receipt_id}</span>
        </Row>
        <Row k="payment_id">
          <span className="font-mono">{receipt.payment_id}</span>
        </Row>
        <Row k="Betrag">
          <span className="font-mono">{formatSats(receipt.amount_sat)}</span>
        </Row>
        <Row k="Bezahlt">
          <span className="font-mono">{formatSats(receipt.paid_amount_sat)}</span>
        </Row>
        <Row k="Bezahlt am">
          <span className="font-mono">{formatDayTime(receipt.paid_at)}</span>
        </Row>
        <Row k="Referenz">
          <span className="font-mono">{receipt.reference || "—"}</span>
        </Row>
        <Row k="Beschreibung">{receipt.description || "—"}</Row>
        <Row k="Rail">
          <span className="font-mono">{receipt.rail}</span>
        </Row>
        <Row k="Journal-Seq">
          <span className="font-mono">{receipt.audit?.journal_seq ?? "—"}</span>
        </Row>
        <Row k="Record-Hash">
          <span className="font-mono break-all">{receipt.audit?.record_hash ?? "—"}</span>
        </Row>
        <Row k="Erstellt">
          <span className="font-mono">{formatDayTime(receipt.created_at)}</span>
        </Row>
      </dl>
      <details className="text-2xs">
        <summary className="cursor-pointer text-fg-subtle">Roh-JSON</summary>
        <pre className="mt-1 overflow-x-auto rounded-sm bg-bg-1 p-2 font-mono text-2xs text-fg-muted">
          {JSON.stringify(receipt, null, 2)}
        </pre>
      </details>
    </div>
  );
}

/* ---------- Kopierbares Feld ---------- */

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState<"idle" | "ok" | "fail">("idle");
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard-API nicht verfügbar");
      await navigator.clipboard.writeText(value);
      setCopied("ok");
    } catch {
      setCopied("fail");
    }
    window.setTimeout(() => setCopied("idle"), 2_000);
  };
  return (
    <div className="w-full">
      <div className="mb-1 text-2xs font-semibold uppercase tracking-[0.08em] text-fg-subtle">{label}</div>
      <div className="flex gap-2">
        <Input
          readOnly
          value={value}
          aria-label={label}
          onFocus={(e) => e.currentTarget.select()}
          className="text-2xs"
        />
        <Button
          type="button"
          variant="outline"
          onClick={copy}
          aria-label={`${label} kopieren`}
          className="shrink-0 h-9"
        >
          {copied === "ok" ? <Check size={14} className="text-pos" /> : <Copy size={14} />}
          {copied === "ok" ? "Kopiert" : copied === "fail" ? "Manuell kopieren" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

/* ---------- Liste der letzten Requests ---------- */

function RecentRequests({
  list,
  activeId,
  onSelect,
  onReload,
}: {
  list: AsyncState<PayRequest[]>;
  activeId: string | null;
  onSelect: (req: PayRequest) => void;
  onReload: () => void;
}) {
  return (
    <Card padded>
      <CardHeader
        title="Letzte Requests"
        subtitle={`Die letzten ${LIST_LIMIT} Zahlungsanforderungen · Klick lädt den Request in die Ansicht`}
        right={
          <Button size="sm" variant="ghost" onClick={onReload} aria-label="Liste neu laden">
            <RefreshCw size={12} /> Neu laden
          </Button>
        }
      />
      {list.state === "loading" && <div className="text-xs text-fg-muted">Lade …</div>}
      {list.state === "error" && (
        <ErrorLine
          label="Liste nicht ladbar"
          message={`${list.error.kind} · ${list.error.message}`}
          onRetry={onReload}
        />
      )}
      {list.state === "ready" && list.data.length === 0 && (
        <div className="text-xs text-fg-muted">Noch keine Zahlungsanforderungen.</div>
      )}
      {list.state === "ready" && list.data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-2xs uppercase tracking-wider text-fg-subtle">
                <th className="py-1.5 pr-3 font-semibold">Status</th>
                <th className="py-1.5 pr-3 font-semibold">Betrag</th>
                <th className="py-1.5 pr-3 font-semibold">Referenz</th>
                <th className="py-1.5 pr-3 font-semibold">Beschreibung</th>
                <th className="py-1.5 pr-3 font-semibold">Zeit</th>
                <th className="py-1.5 font-semibold" />
              </tr>
            </thead>
            <tbody>
              {list.data.map((r) => {
                const active = r.payment_id === activeId;
                return (
                  <tr
                    key={r.payment_id}
                    onClick={() => onSelect(r)}
                    className={cn(
                      "cursor-pointer border-t border-line-subtle transition-colors hover:bg-bg-2",
                      active && "bg-bg-3",
                    )}
                  >
                    <td className="py-1.5 pr-3">
                      <Badge tone={payStatusTone(r.status)} dot>
                        {payStatusLabel(r.status)}
                      </Badge>
                    </td>
                    <td className="py-1.5 pr-3 font-mono text-fg">{formatSats(r.amount_sat)}</td>
                    <td className="py-1.5 pr-3 font-mono">{r.reference || "—"}</td>
                    <td className="py-1.5 pr-3 max-w-[28ch] truncate" title={r.description}>
                      {r.description || "—"}
                    </td>
                    <td className="py-1.5 pr-3 font-mono text-fg-muted whitespace-nowrap">
                      {formatDayTime(r.created_at)}
                    </td>
                    <td className="py-1.5 text-right">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelect(r);
                        }}
                        aria-label={`Request ${r.payment_id} laden`}
                        className="rounded-xs border border-line-subtle bg-bg-2 px-1.5 py-0.5 text-2xs text-fg-muted hover:text-fg"
                      >
                        Laden
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/* ---------- Fehlerzeile — nie stiller Fehlzustand ---------- */

function ErrorLine({
  label,
  message,
  onRetry,
}: {
  label: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-2 rounded-sm border border-neg/30 bg-neg/10 px-3 py-2 text-xs text-neg break-words"
    >
      <span>
        <span className="font-semibold">{label}:</span>{" "}
        <span className="font-mono">{message}</span>
      </span>
      {onRetry && (
        <Button size="sm" variant="ghost" onClick={onRetry} className="ml-auto text-neg">
          Erneut
        </Button>
      )}
    </div>
  );
}
