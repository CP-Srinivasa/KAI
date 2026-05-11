"""Append-only JSONL storage for regime snapshots.

Pattern follows ``app/audit/kai_audit_service.py:append_event`` and
``app/alerts/audit.py:append_outcome_annotation`` (V-DB5 B-K2):
``portalocker.Lock`` wraps every write so concurrent writers (manual CLI
trigger ↔ scheduled timer ↔ test fixture) cannot interleave half-written
lines into the same file.

File layout: one JSONL per asset, one snapshot per line, oldest-first.
    artifacts/regime_state/btc_regime.jsonl
    artifacts/regime_state/eth_regime.jsonl

Idempotency at the storage layer is intentionally minimal — duplicate-hour
detection is the service's responsibility, not the storage's. The reader
honours the *last* snapshot per timestamp, so a re-run within the same
hour does not corrupt history.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import portalocker

from app.regime.models import RegimeClass, RegimeSnapshot

DEFAULT_REGIME_DIR = Path("artifacts/regime_state")


def regime_jsonl_filename(asset: str) -> str:
    """Lower-cased canonical filename for an asset's regime JSONL."""
    return f"{asset.lower()}_regime.jsonl"


def resolve_regime_path(asset: str, base_dir: str | Path = DEFAULT_REGIME_DIR) -> Path:
    """Return the JSONL path for an asset under base_dir."""
    return Path(base_dir) / regime_jsonl_filename(asset)


def append_regime_snapshot(
    snapshot: RegimeSnapshot,
    base_dir: str | Path = DEFAULT_REGIME_DIR,
) -> Path:
    """Append a regime snapshot to the asset's JSONL file.

    Locked write via portalocker (V-DB5 B-K2 pattern). Creates parent
    directory on demand. Returns the resolved file path.
    """
    p = resolve_regime_path(snapshot.asset, base_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(snapshot.to_json_dict(), ensure_ascii=False)
    with portalocker.Lock(p, mode="a", encoding="utf-8") as f:
        f.write(line + "\n")
    return p


def _snapshot_from_dict(data: dict[str, Any]) -> RegimeSnapshot | None:
    """Reverse of ``RegimeSnapshot.to_json_dict``. Returns None on bad data."""
    try:
        regime = RegimeClass(data["regime"])
    except (KeyError, ValueError):
        return None
    pending_raw = data.get("pending_regime")
    pending: RegimeClass | None = None
    if isinstance(pending_raw, str):
        try:
            pending = RegimeClass(pending_raw)
        except ValueError:
            pending = None
    try:
        return RegimeSnapshot(
            asset=data["asset"],
            timestamp=data["timestamp"],
            regime=regime,
            vol_class=data.get("vol_class", "vol_normal"),
            confidence=float(data.get("confidence", 1.0)),
            adx=_optional_float(data.get("adx")),
            plus_di=_optional_float(data.get("plus_di")),
            minus_di=_optional_float(data.get("minus_di")),
            rv_24h=_optional_float(data.get("rv_24h")),
            atr_zscore=_optional_float(data.get("atr_zscore")),
            pending_regime=pending,
            pending_consecutive=int(data.get("pending_consecutive", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def load_regime_snapshots(
    asset: str,
    base_dir: str | Path = DEFAULT_REGIME_DIR,
) -> list[RegimeSnapshot]:
    """Load all snapshots for an asset (oldest-first). Missing file → []."""
    p = resolve_regime_path(asset, base_dir)
    if not p.exists():
        return []
    out: list[RegimeSnapshot] = []
    with p.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            snap = _snapshot_from_dict(data)
            if snap is not None:
                out.append(snap)
    return out


def latest_regime_snapshot(
    asset: str,
    base_dir: str | Path = DEFAULT_REGIME_DIR,
) -> RegimeSnapshot | None:
    """Return the most recent snapshot for an asset, or None.

    "Most recent" = last successfully parsed line in the file. Re-runs
    within the same hour are honoured: the latest write wins even if an
    older entry shares the same timestamp.
    """
    snapshots = load_regime_snapshots(asset, base_dir)
    return snapshots[-1] if snapshots else None
