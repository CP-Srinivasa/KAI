"""Lightning als erster Rail-Adapter (ADR 0017 §7).

Wrappt ``app.lightning.client``/``adapter``. Der Adapter uebersetzt in beide
Richtungen und haelt dabei drei Zusagen, die im Bestand fehlten:

1. **Ein Timeout ist keine Ablehnung.** Jede Transportstoerung (httpx-Timeout,
   TLS, 5xx, unlesbares JSON) wird ``RailOutcome.UNKNOWN`` — nie ``FAILED``.
   Der Bestand journallierte ``error`` und gab damit einen Retry frei; der
   25k-Spend vom 07-02 war genau das.
2. **Kein Send ohne Fee-Limit.** ``client.py`` setzt ``fee_limit`` nur, wenn es
   > 0 ist; bei 0 laesst lnd das Feld weg und routet ohne Obergrenze. Der
   Adapter erzwingt > 0, bevor er den Client ueberhaupt ruft.
3. **Kein Send ausser in LIVE.** SHADOW liest, SIMULATION beruehrt den Node
   nicht. ``pay`` prueft das selbst, statt sich auf den Aufrufer zu verlassen.

**Warum die Importe von ``app.lightning`` verzoegert sind.** Ein Top-Level-
Import schloesse den Paketzyklus ``payments -> lightning -> truth -> audit ->
payments`` (``app.audit`` liest seit ADR §2 aus ``app.payments``). Der Import im
Funktionsrumpf existiert zur Importzeit nicht und kann deshalb keinen
Importzyklus ausloesen; ``tests/unit/test_payment_dependency_direction.py``
haelt beides fest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.core.lightning_settings import LightningSettings
from app.core.payment_settings import PaymentSettings
from app.payments.enums import RailOutcome, SettlementFinality
from app.payments.models import Invoice, PaymentAttempt, PaymentIntent, Quote
from app.payments.rail import (
    DecodedDestination,
    DedupGuarantee,
    InvoiceRequest,
    InvoiceStatus,
    RailAction,
    RailCapabilities,
    RailError,
    RailHealth,
    RailLookup,
    RailPaymentList,
    RailResult,
)
from app.payments.rails.lightning_mapping import (
    destination_from_payreq,
    lookup_from_payment,
    normalise_payment_hash,
    payments_from_rows,
    result_from_send,
    sat,
    sha,
    wallet_is_locked,
)
from app.payments.rails.lightning_scan import scan_payments

if TYPE_CHECKING:  # pragma: no cover - nur fuer die Typpruefung
    from app.lightning.client import LndRestClient


class LightningRail:
    """Der lnd-Adapter. Kennt ``app.payments`` nur ueber ``rail``/``models``."""

    name = "lightning"

    def __init__(
        self,
        *,
        payment_settings: PaymentSettings,
        lightning_settings: LightningSettings,
        client_factory: Any = None,
    ) -> None:
        self._payments = payment_settings
        self._ln = lightning_settings
        #: Nur fuer Tests: erlaubt einen gefakten Client, ohne einen zweiten
        #: Credential-Pfad einzufuehren. Produktiv baut ``_client`` selbst.
        self._client_factory = client_factory

    # -- Client ------------------------------------------------------------- #

    def _client(self, scope: str) -> LndRestClient:
        """Baue einen Client mit GENAU einer Capability.

        Es gibt bewusst keinen Fallback auf das Read-Macaroon: eine fehlende
        Capability muss laut fehlschlagen, statt still die
        Ein-Macaroon-fuer-alles-Konfiguration wiederherzustellen.
        """
        if self._client_factory is not None:
            return self._client_factory(scope)  # type: ignore[no-any-return]
        from app.lightning.adapter import _build_client

        return _build_client(self._ln, credential_scope=scope)  # type: ignore[arg-type]

    # -- Selbstauskunft ----------------------------------------------------- #

    def capabilities(self) -> RailCapabilities:
        """Lightning, wie es wirklich ist (ADR §7).

        ``max_inflight_window`` kommt aus den Settings, nicht aus dem
        Invoice-Ablauf: eine abgelaufene Invoice ist unbezahlbar, aber ein
        bereits steckengebliebener HTLC haengt bis zum CLTV-Delta weiter.
        """
        return RailCapabilities(
            name=self.name,
            settlement_finality=SettlementFinality.INSTANT,
            confirmation_depth_required=0,
            reversal_supported=False,
            dedup_guarantee=DedupGuarantee.BY_PAYMENT_HASH,
            max_inflight_window=timedelta(seconds=self._payments.max_inflight_window_s),
            fee_model="routing_fee",
            supported_actions=frozenset({RailAction.PAY_INVOICE, RailAction.CREATE_INVOICE}),
        )

    async def health(self) -> RailHealth:
        """Nie werfen: ein unerreichbarer Node ist ein Befund, kein Fehler."""
        moment = datetime.now(UTC)
        unhealthy = RailHealth(rail=self.name, observed_at=moment)
        if not self._ln.enabled:
            return unhealthy.model_copy(update={"reason": "APP_LN_ENABLED=false"})
        try:
            client = self._client("read")
            state = await client.get_state()
            info = await client.get_info()
        except Exception as exc:  # noqa: BLE001 - jeder Fehler heisst "nicht gesund"
            return unhealthy.model_copy(
                update={"reason": f"{type(exc).__name__}: {str(exc)[:150]}"}
            )
        locked = wallet_is_locked(state)
        return RailHealth(
            rail=self.name,
            reachable=True,
            synced_to_chain=info.synced_to_chain,
            synced_to_graph=info.synced_to_graph,
            wallet_locked=locked,
            observed_at=moment,
            reason=f"state={state}",
        )

    # -- Decode / Quote ----------------------------------------------------- #

    async def decode(self, destination: str) -> DecodedDestination:
        """``decodepayreq`` — die Bindung, gegen die die Allowlist prueft."""
        payment_request = destination.strip()
        if not payment_request:
            raise RailError("empty payment request cannot be decoded")
        try:
            decoded = await self._client("read").decode_pay_req(payment_request=payment_request)
        except Exception as exc:  # noqa: BLE001 - der Rail konnte nicht antworten
            raise RailError(f"decode failed: {type(exc).__name__}: {exc}") from exc
        return destination_from_payreq(decoded, rail=self.name)

    async def quote(self, intent: PaymentIntent) -> Quote:
        """Kostenvorschau — read-only, und immer mit genannter Herkunft.

        Der Client hat heute keine ``queryroutes``/``estimateroutefee``-Methode.
        Statt eine zu erfinden (und damit einen neuen Node-Aufruf im Geldpfad
        einzufuehren, den niemand reviewt hat), rechnet der Adapter aus dem
        konfigurierten ppm-Satz und sagt das im ``estimate_source``. Eine
        Schaetzung, die ihre Herkunft verschweigt, wird spaeter fuer eine
        Messung gehalten.
        """
        amount = intent.amount_requested.minor_units
        ppm_fee = amount * self._payments.fee_limit_default_ppm // 1_000_000
        estimate = min(max(ppm_fee, 1), self._payments.fee_limit_max_sat)
        source = "settings_ppm"

        estimator = getattr(self._client("read"), "estimate_route_fee", None)
        if callable(estimator):
            try:
                observed = await estimator(payment_request=intent.destination)
                estimate = int(observed)
                source = "node_estimate_route_fee"
            except Exception:  # noqa: BLE001 - eine Schaetzung darf nichts blockieren
                estimate = min(max(ppm_fee, 1), self._payments.fee_limit_max_sat)
                source = "settings_ppm"

        return Quote(
            rail=self.name,
            amount=intent.amount_requested,
            fee_estimate=sat(estimate),
            valid_until=datetime.now(UTC) + timedelta(minutes=5),
            estimate_source=source,
        )

    # -- Senden ------------------------------------------------------------- #

    async def pay(self, intent: PaymentIntent, attempt: PaymentAttempt) -> RailResult:
        """BOLT11 zahlen. Der einzige Aufruf, der Geld bewegt.

        Drei Tore VOR dem Client: Modus, Kill-Switch, Fee-Limit. Sie stehen
        hier und nicht nur im Service, weil der Adapter die letzte Stelle ist,
        die den Node kennt — ein zweiter Aufrufer wuerde sie sonst umgehen.
        """
        moment = datetime.now(UTC)
        if self._payments.mode != "live":
            raise RailError(
                f"pay refused: payment mode is {self._payments.mode!r}; "
                "SHADOW is read-only and SIMULATION never touches the node"
            )
        if not self._ln.pay_enabled:
            raise RailError("pay refused: APP_LN_PAY_ENABLED=false (wired kill-switch)")
        fee_limit = intent.fee_limit.minor_units
        if fee_limit <= 0:
            raise RailError(
                "pay refused: fee_limit must be > 0 — lnd omits the fee limit entirely "
                "when it is 0, which routes without an upper bound"
            )

        try:
            response = await self._client("payment").pay_invoice(
                payment_request=intent.destination,
                fee_limit_sat=fee_limit,
            )
        except Exception as exc:  # noqa: BLE001 - ausbleibende Antwort ist keine Aussage
            # NIE FAILED: der Send kann drausssen sein. Der Service macht daraus
            # RECONCILIATION_REQUIRED, der Reconciler fragt den Node.
            return RailResult(
                rail=self.name,
                outcome=RailOutcome.UNKNOWN,
                rail_dedup_key=attempt.rail_dedup_key,
                observed_at=moment,
                raw_status=type(exc).__name__[:32],
            )
        return result_from_send(response, rail=self.name, attempt=attempt, moment=moment)

    # -- Nachschauen -------------------------------------------------------- #

    async def lookup(self, rail_dedup_key: str) -> RailLookup:
        """``GET /v1/payments`` nach ``payment_hash`` durchsuchen (ADR §8)."""
        moment = datetime.now(UTC)
        wanted = normalise_payment_hash(rail_dedup_key)
        unknown = RailLookup(
            rail=self.name,
            found=False,
            outcome=RailOutcome.UNKNOWN,
            rail_dedup_key=wanted or rail_dedup_key,
            observed_at=moment,
        )
        try:
            scan = await scan_payments(
                self._client("read"),
                include_incomplete=True,
                keep=lambda row: row.payment_hash == wanted,
                stop_after_first_hit=True,
            )
        except Exception:  # noqa: BLE001 - kein Node-Kontakt heisst nicht "nichts da"
            return unknown
        if not scan.rows:
            return unknown
        return lookup_from_payment(scan.rows[0], rail=self.name, moment=moment)

    async def list_payments(self, since: datetime) -> RailPaymentList:
        """Alle erfolgreichen Sends, die lnd kennt (ADR §8, Rueckwaerts-Richtung).

        **``since`` kann dieser Rail nicht einhalten, und er sagt das.**
        ``app/lightning/client.py`` reicht aus ``ListPayments`` nur
        ``payment_hash``, ``status``, ``failure_reason``, ``value_sat``,
        ``fee_sat`` und ``payment_index`` durch — kein ``creation_date``. Einen
        Zeitstempel zu erfinden waere schlimmer als keiner: der Reconciler
        wuerde ein Fenster ANNEHMEN, das nie geprueft wurde. Stattdessen steht
        ``window_enforced=False`` in der Antwort, und der Reconciler meldet jede
        Waise genau einmal — bei der Inbetriebnahme also einmalig die
        Alt-Historie, danach Ruhe.
        """
        moment = datetime.now(UTC)
        try:
            scan = await scan_payments(
                self._client("read"),
                include_incomplete=False,
                keep=lambda row: str(row.status).strip().upper() == "SUCCEEDED",
            )
        except Exception:  # noqa: BLE001 - kein Node-Kontakt heisst nicht "nichts da"
            return RailPaymentList(rail=self.name, window_enforced=False, complete=False)
        return RailPaymentList(
            rail=self.name,
            payments=payments_from_rows(scan.rows, rail=self.name, moment=moment),
            window_enforced=False,
            complete=scan.complete,
        )

    # -- Empfangen ---------------------------------------------------------- #

    async def create_invoice(self, request: InvoiceRequest) -> Invoice:
        """``add_invoice`` im INVOICE-Scope — kein Sende-Credential noetig."""
        try:
            response = await self._client("invoice").add_invoice(
                value_sat=request.amount.minor_units,
                expiry_seconds=request.expiry_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raise RailError(f"create_invoice failed: {type(exc).__name__}: {exc}") from exc
        ref_hash = normalise_payment_hash(response.get("r_hash"))
        if not ref_hash:
            raise RailError("add_invoice returned no usable r_hash")
        return Invoice(
            rail=self.name,
            ref_hash=ref_hash,
            amount=request.amount,
            payee_hash=sha(f"self:{self.name}"),
            expires_at=datetime.now(UTC) + timedelta(seconds=request.expiry_seconds),
            memo_hash=request.memo_hash,
        )

    async def invoice_status(self, ref_hash: str) -> InvoiceStatus:
        """Nie werfen: eine unbeantwortete Frage heisst "noch nicht bezahlt"."""
        moment = datetime.now(UTC)
        wanted = normalise_payment_hash(ref_hash)
        pending = InvoiceStatus(
            rail=self.name,
            ref_hash=wanted or ref_hash,
            settled=False,
            observed_at=moment,
        )
        try:
            invoices = await self._client("invoice").list_invoices()
        except Exception:  # noqa: BLE001
            return pending
        for raw in invoices:
            if not isinstance(raw, dict):
                continue
            if normalise_payment_hash(raw.get("r_hash")) != wanted:
                continue
            if not bool(raw.get("settled", False)):
                return pending
            settled_index = int(raw.get("settle_date") or 0)
            return InvoiceStatus(
                rail=self.name,
                ref_hash=wanted,
                settled=True,
                observed_at=moment,
                amount_paid=sat(int(raw.get("amt_paid_sat") or 0)),
                settled_at=(
                    datetime.fromtimestamp(settled_index, tz=UTC) if settled_index > 0 else None
                ),
            )
        return pending


__all__ = ["LightningRail"]
