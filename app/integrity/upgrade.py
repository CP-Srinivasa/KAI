"""L3 OTS proof upgrade + status (KAI L3 — make the daily anchor's .ots real).

The anchor (:mod:`app.integrity.anchor`) writes a PENDING ``.ots``: it carries the
calendar commitments but no Bitcoin attestation yet — a calendar aggregates many
digests into one Bitcoin transaction that is mined HOURS later. This module does
the asynchronous second half:

  * :func:`upgrade_pending_proofs` — re-queries each pending calendar for the
    now-mined Bitcoin attestation and rewrites the ``.ots`` once it is
    Bitcoin-verifiable. Fail-soft: a calendar outage or a not-yet-mined commitment
    leaves the proof pending; the pass never raises.
  * :func:`read_proof_info` — classifies a proof as ``pending`` /
    ``confirmed`` / ``incomplete`` / ``unreadable`` for the read surface
    (``get_integrity_status``), so the dashboard can honestly distinguish a
    submitted-but-not-yet-mined proof from a real Bitcoin-anchored one.

Read-only w.r.t. KAI's audit SSOT, no capital path. The ``opentimestamps`` lib is
imported lazily so importing this module never forces the dependency until an
upgrade/classification actually runs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.integrity.anchor import AnchorUnavailableError

if TYPE_CHECKING:  # pragma: no cover — typing only
    from opentimestamps.core.timestamp import Timestamp

logger = logging.getLogger(__name__)

_OTS_TIMEOUT_S = 15.0

# Proof states (mirrors the OTS notion: a proof is only a real anchor once a
# Bitcoin attestation is present; until then it is a pending calendar commitment).
PENDING = "pending"
CONFIRMED = "confirmed"
INCOMPLETE = "incomplete"  # stamped but no attestation at all (should not happen)
UNREADABLE = "unreadable"  # not a parseable .ots


@dataclass(frozen=True)
class ProofInfo:
    """Classification of one ``.ots`` proof."""

    state: str
    bitcoin_height: int | None = None


@dataclass(frozen=True)
class UpgradeReport:
    """Outcome of one upgrade pass over a proofs directory."""

    scanned: int = 0
    already_confirmed: int = 0
    upgraded: int = 0
    still_pending: int = 0
    failed: int = 0


def classify_timestamp(ts: Timestamp) -> ProofInfo:
    """Classify a deserialized OTS timestamp (pure; lib already imported).

    A Bitcoin attestation anywhere in the tree → ``confirmed`` (lowest height
    reported). Otherwise a pending calendar attestation → ``pending``. Neither →
    ``incomplete``.
    """
    from opentimestamps.core.notary import (
        BitcoinBlockHeaderAttestation,
        PendingAttestation,
    )

    has_pending = False
    height: int | None = None
    for _msg, att in ts.all_attestations():
        if isinstance(att, BitcoinBlockHeaderAttestation):
            height = att.height if height is None else min(height, att.height)
        elif isinstance(att, PendingAttestation):
            has_pending = True
    if height is not None:
        return ProofInfo(state=CONFIRMED, bitcoin_height=height)
    if has_pending:
        return ProofInfo(state=PENDING)
    return ProofInfo(state=INCOMPLETE)


def _load_detached(path: Path) -> Any:
    from opentimestamps.core.serialize import BytesDeserializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile

    blob = path.read_bytes()
    return DetachedTimestampFile.deserialize(BytesDeserializationContext(blob))


def _save_detached(detached: Any, path: Path) -> None:
    from opentimestamps.core.serialize import BytesSerializationContext

    ctx = BytesSerializationContext()
    detached.serialize(ctx)
    path.write_bytes(ctx.getbytes())


def read_proof_info(path: Path) -> ProofInfo:
    """Classify the ``.ots`` at ``path`` (fail-soft → ``unreadable`` on any error)."""
    try:
        detached = _load_detached(path)
    except ImportError:
        raise
    except Exception as exc:  # noqa: BLE001 — a corrupt proof must not crash a read
        logger.info("[ots] unreadable proof %s: %s", path.name, exc)
        return ProofInfo(state=UNREADABLE)
    return classify_timestamp(detached.timestamp)


def _walk(ts: Timestamp) -> Any:
    yield ts
    for sub in ts.ops.values():
        yield from _walk(sub)


def _resolve_calendar_factory(
    calendar_factory: Callable[[str], Any] | None,
) -> Callable[[str], Any]:
    if calendar_factory is not None:
        return calendar_factory
    try:
        from opentimestamps.calendar import RemoteCalendar
    except ImportError as exc:  # optional dep not installed
        raise AnchorUnavailableError(
            "opentimestamps library not installed (pip install -e .)"
        ) from exc
    # Wrap in a lambda so the return is a typed Callable (the untyped lib class
    # would otherwise surface as Any → mypy no-any-return).
    return lambda uri: RemoteCalendar(uri)


def _try_upgrade(ts: Timestamp, factory: Callable[[str], Any], timeout: float) -> None:
    """Best-effort: for each pending attestation, fetch the mined upgrade from its
    calendar and merge it in. Per-attestation errors (calendar down, not yet
    mined) are swallowed — the proof simply stays pending."""
    from opentimestamps.core.notary import PendingAttestation

    for node in list(_walk(ts)):
        for att in list(node.attestations):
            if not isinstance(att, PendingAttestation):
                continue
            try:
                upgraded = factory(att.uri).get_timestamp(node.msg, timeout=timeout)
                node.merge(upgraded)
            except Exception as exc:  # noqa: BLE001 — pending/outage is expected
                logger.info("[ots] still pending at %s: %s", att.uri, exc)


def upgrade_pending_proofs(
    proofs_dir: Path | str,
    *,
    calendar_factory: Callable[[str], Any] | None = None,
    timeout: float = _OTS_TIMEOUT_S,
) -> UpgradeReport:
    """Upgrade every pending ``.ots`` in ``proofs_dir`` to a Bitcoin attestation
    where the calendar's aggregation has been mined. Never raises (except a missing
    opentimestamps library, which is a hard config error surfaced to the runner).

    Returns counts; already-Bitcoin-confirmed proofs are skipped (no calendar call).
    """
    out_dir = Path(proofs_dir)
    try:
        proofs = sorted(out_dir.glob("*.ots"))
    except OSError as exc:
        logger.warning("[ots] proofs dir unreadable: %s", exc)
        return UpgradeReport()

    if not proofs:
        return UpgradeReport(scanned=0)

    factory = _resolve_calendar_factory(calendar_factory)
    scanned = already = upgraded = still_pending = failed = 0
    for path in proofs:
        scanned += 1
        try:
            detached = _load_detached(path)
        except ImportError:
            raise
        except Exception as exc:  # noqa: BLE001 — skip a corrupt proof, keep going
            logger.info("[ots] skip unreadable %s: %s", path.name, exc)
            failed += 1
            continue

        info = classify_timestamp(detached.timestamp)
        if info.state == CONFIRMED:
            already += 1
            continue
        if info.state != PENDING:
            failed += 1
            continue

        _try_upgrade(detached.timestamp, factory, timeout)
        if classify_timestamp(detached.timestamp).state == CONFIRMED:
            try:
                _save_detached(detached, path)
                upgraded += 1
            except OSError as exc:
                logger.warning("[ots] could not persist upgraded proof %s: %s", path.name, exc)
                failed += 1
        else:
            still_pending += 1

    return UpgradeReport(
        scanned=scanned,
        already_confirmed=already,
        upgraded=upgraded,
        still_pending=still_pending,
        failed=failed,
    )
