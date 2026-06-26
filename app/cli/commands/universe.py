"""CLI: momentum-universe — build the own-data universe + persist a snapshot.

READ-ONLY: NO trades, NO capital. Computes the most-traded / best-performer
universe from sanctioned exchange data (Bybit 24h volume rank + daily-OHLCV
multi-window returns) and appends a snapshot to the candidates ledger. ``show``
reads the latest persisted snapshot offline (no network).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from app.observability.momentum_universe_builder import MomentumUniverseSource

universe_app = typer.Typer(help="Momentum-Universe (read-only) commands.", no_args_is_help=True)

_DEFAULT_LEDGER = Path("artifacts/momentum_universe_candidates.jsonl")


def _resolve_source(provider: str) -> MomentumUniverseSource:
    key = provider.strip().lower()
    if key == "bybit":
        from app.market_data.bybit_adapter import BybitAdapter

        return BybitAdapter()
    raise typer.BadParameter(f"unknown provider: {provider!r} (supported: bybit)")


@universe_app.command("build")
def build(
    provider: Annotated[str, typer.Option(help="Exchange data source.")] = "bybit",
    top_n: Annotated[int, typer.Option(help="Symbols to keep in the universe.")] = 15,
    universe_limit: Annotated[int, typer.Option(help="Volume-top symbols to consider.")] = 50,
    ledger: Annotated[Path, typer.Option(help="JSONL snapshot path.")] = _DEFAULT_LEDGER,
) -> None:
    """Build the universe from live exchange data + append a snapshot. No trades."""
    from datetime import UTC, datetime

    from app.observability.momentum_universe_builder import build_universe
    from app.observability.momentum_universe_ledger import append_snapshot

    source = _resolve_source(provider)
    ranked = asyncio.run(build_universe(source, top_n=top_n, universe_limit=universe_limit))
    record = append_snapshot(ledger, ranked, now=datetime.now(UTC))
    typer.echo(json.dumps(record, indent=2))


@universe_app.command("show")
def show(
    ledger: Annotated[Path, typer.Option(help="JSONL snapshot path.")] = _DEFAULT_LEDGER,
) -> None:
    """Print the latest persisted universe snapshot (offline)."""
    from app.observability.momentum_universe_ledger import read_latest

    latest = read_latest(ledger)
    if latest is None:
        typer.echo(json.dumps({"available": False, "reason": "no_snapshot"}))
        return
    typer.echo(json.dumps(latest, indent=2))


_AUDIT = Path("artifacts/paper_execution_audit.jsonl")
_ROT_STATE = Path("artifacts/asset_rotation_state.json")
_ROT_SHADOW = Path("artifacts/asset_rotation_shadow.jsonl")


@universe_app.command("rotate-shadow")
def rotate_shadow(
    audit: Annotated[Path, typer.Option(help="Paper-execution audit JSONL.")] = _AUDIT,
    state: Annotated[Path, typer.Option(help="Rotation FSM state JSON.")] = _ROT_STATE,
    shadow_log: Annotated[Path, typer.Option(help="Rotation shadow JSONL.")] = _ROT_SHADOW,
    last_n: Annotated[int, typer.Option(help="Closes window.")] = 200,
) -> None:
    """G1: evaluate asset-rotation decisions on paper data (shadow-only). No trades."""
    from datetime import UTC, datetime

    from app.learning.asset_rotation_shadow import run_rotation_shadow

    record = run_rotation_shadow(
        audit_path=audit,
        state_path=state,
        shadow_log_path=shadow_log,
        last_n=last_n,
        now=datetime.now(UTC),
    )
    typer.echo(json.dumps(record, indent=2))


@universe_app.command("rotate-show")
def rotate_show(
    state: Annotated[Path, typer.Option(help="Rotation FSM state JSON.")] = _ROT_STATE,
) -> None:
    """Print the current per-asset rotation FSM state (offline)."""
    from app.learning.asset_rotation_shadow import load_state

    loaded = load_state(state)
    out = {
        sym: {"status": st.status.value, "flagged_runs": st.flagged_runs}
        for sym, st in sorted(loaded.items())
    }
    typer.echo(json.dumps({"count": len(out), "assets": out}, indent=2))


@universe_app.command("feed-run")
def feed_run() -> None:
    """G2: feed the universe into PAPER once (gated by MOMENTUM_UNIVERSE_FEED_ENABLED)."""
    import asyncio

    from app.observability.momentum_universe_feeder import run_momentum_feeder

    result = asyncio.run(run_momentum_feeder())
    typer.echo(json.dumps(result, indent=2))


_COHORT_OUTCOMES = Path("artifacts/momentum_cohort_outcomes.jsonl")


@universe_app.command("cohort-outcomes")
def cohort_outcomes(
    audit: Annotated[Path, typer.Option(help="Paper-execution audit JSONL.")] = _AUDIT,
    out: Annotated[Path, typer.Option(help="Outcomes JSONL output path.")] = _COHORT_OUTCOMES,
    cohort: Annotated[
        str, typer.Option(help="signal_source cohort to extract.")
    ] = "momentum_universe",
) -> None:
    """G3: extract resolved cohort outcomes (symbol/entry_ts/net_bps) for the evidence eval."""
    from app.observability.edge_report import load_audit_events
    from app.observability.momentum_cohort_outcomes import extract_cohort_outcomes

    rows = extract_cohort_outcomes(load_audit_events(audit), cohort=cohort)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    typer.echo(json.dumps({"cohort": cohort, "resolved": len(rows), "out": str(out)}, indent=2))


_CROSSCHECK = Path("artifacts/momentum_crosscheck.jsonl")


@universe_app.command("crosscheck")
def crosscheck(
    provider: Annotated[str, typer.Option(help="OHLCV source.")] = "bybit",
    top_n: Annotated[int, typer.Option(help="Universe symbols to cross-check.")] = 15,
    out: Annotated[Path, typer.Option(help="Cross-check ledger path.")] = _CROSSCHECK,
) -> None:
    """G4: own momentum rank vs own-TA rating cross-check (informational, no trades)."""
    import asyncio
    from datetime import UTC, datetime

    from app.observability.momentum_crosscheck import append_crosscheck, build_crosscheck

    source = _resolve_source(provider)
    rows = asyncio.run(build_crosscheck(source, top_n=top_n))
    record = append_crosscheck(out, rows, now=datetime.now(UTC))
    typer.echo(json.dumps(record, indent=2))


@universe_app.command("crosscheck-show")
def crosscheck_show(
    ledger: Annotated[Path, typer.Option(help="Cross-check ledger path.")] = _CROSSCHECK,
) -> None:
    """Print the latest persisted cross-check snapshot (offline)."""
    from app.observability.momentum_crosscheck import read_latest_crosscheck

    latest = read_latest_crosscheck(ledger)
    if latest is None:
        typer.echo(json.dumps({"available": False, "reason": "no_snapshot"}))
        return
    typer.echo(json.dumps(latest, indent=2))
