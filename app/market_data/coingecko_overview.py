"""CoinGecko market-overview graduation (G1, Source-Intake §9 Graduation-Gate).

Hintergrund
===========
Die Exploration-Phase (ADR-0006, Coverage-Report 2026-06-17) hat ``coingecko``
als stärksten Free-Win bestätigt: ``/coins/markets`` liefert keylos
``market_cap_rank``, ``market_cap`` und ``price_change_percentage_30d`` — Felder,
die der bestehende Preis-Adapter (``get_ticker``) aus *genau derselben Antwort*
bereits zieht, aber verwirft. Diese Schicht hebt sie als read-only Markt-Kontext
heraus (Universe-Ranking / Rel-Stärke-Input gegen die BTC-Monokultur).

Measure-first + default-off (V5-Disziplin, vgl. ``evidence_settings``)
=====================================================================
- ``enabled=False`` (default): NICHTS im Live-Loop liest diese Schicht. Ein
  frisches Deployment ändert das Verhalten nicht, bis der Operator opt-in macht.
- Ein **entkoppelter** Refresh-Service (``scripts/coingecko_overview_refresh``,
  systemd-Timer, operator-gated) schreibt periodisch einen warmen Snapshot
  (``snapshot_path``) + eine append-only **Shadow-Spur** (``shadow_log_path``),
  sodass die *Materialität* der Felder gemessen werden kann, BEVOR sie das
  Universe/den Screener beeinflussen. Der Loop zieht hier KEIN Inline-Netz-I/O.
- ``ttl_seconds`` gated stale Snapshots aus (fail-safe, falls der Refresh
  ausfällt). ``market_cap_rank``/``market_cap`` bewegen sich langsam → 15 min.

Increment 1 (dieses Modul + Adapter-Methode + Refresh + Tests) hat NULL
Laufzeit-Einfluss: kein Produktiv-Modul importiert es im Loop-Pfad. Die
Verdrahtung in ``asset_universe`` ist Increment 2 — erst nach Shadow-Sichtung.

Bewusst NICHT als Bayes-Evidence modelliert (kein ``source_trust``): das ist
Markt-Kontext fürs Ranking, keine direktionale Confidence-Verschiebung.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")


@dataclass(frozen=True)
class CoinGeckoMarketOverview:
    """Read-only Markt-Kontext-Record aus ``/coins/markets`` (G1).

    Felder sind ``None``-tolerant: CoinGecko kann einzelne Werte (z.B.
    ``price_change_percentage_30d``) auslassen, ohne dass der Record wertlos
    wird — der ``market_cap_rank`` ist der Kern-Nutzen.
    """

    symbol: str
    timestamp_utc: str
    market_cap_rank: int | None
    market_cap: float | None
    price_change_pct_30d: float | None
    source: str = "coingecko"


class CoinGeckoOverviewSettings(BaseSettings):
    """Default-off, measure-first Settings der G1-Overview-Schicht.

    Bewusst NICHT in ``AppSettings`` registriert: ``app/core/settings.py`` ist
    ein geratchter God-File (down-only). Der Refresh-Service instanziiert diese
    Settings direkt; der env-Prefix ``APP_COINGECKO_OVERVIEW_`` macht sie
    .env-überschreibbar wie die V5-Schichten.

    Free-Tier per Default (kein ``api_key``): die Sandbox hat bewiesen, dass
    ``/coins/markets`` keylos genau diese Felder liefert. Ein optionaler Pro-Key
    (``APP_COINGECKO_OVERVIEW_API_KEY``) bleibt möglich, ist aber kein Muss.
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_COINGECKO_OVERVIEW_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    ttl_seconds: float = Field(default=900.0, gt=0.0)
    snapshot_path: Path = Field(default=Path("artifacts/coingecko_overview_cache.json"))
    shadow_log_path: Path = Field(default=Path("artifacts/coingecko_overview_shadow.jsonl"))
    refresh_timeout_seconds: float = Field(default=10.0, gt=0.0)
    api_key: str | None = Field(default=None)
    # CSV statt list[str]: vermeidet pydantic-settings JSON-env-Parsing für
    # komplexe Typen (eine häufige .env-Falle). ``symbols`` parst defensiv.
    symbols_csv: str = Field(default=",".join(_DEFAULT_SYMBOLS))

    @property
    def symbols(self) -> list[str]:
        syms = [s.strip().upper() for s in self.symbols_csv.split(",") if s.strip()]
        return syms or list(_DEFAULT_SYMBOLS)


class CoinGeckoOverviewStore:
    """Atomare JSON-Persistenz für Overview-Snapshots (key = canonical symbol).

    Spiegelt ``FundingSnapshotStore``: ``write_many`` (Refresh) + ``read_all`` /
    ``read`` (Increment-2-Loop-Pfad). Lese-Fehler ⇒ leeres Dict, kein raise.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def write_many(self, overviews: list[CoinGeckoMarketOverview]) -> int:
        payload: dict[str, Any] = {
            "schema": _SCHEMA_VERSION,
            "written_at_utc": datetime.now(UTC).isoformat(),
            "snapshots": {ov.symbol: asdict(ov) for ov in overviews},
        }
        count = len(payload["snapshots"])
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".cg_overview_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return count

    def read_all(self) -> dict[str, CoinGeckoMarketOverview]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("[cg-overview-store] corrupt snapshot file: %s", self._path)
            return {}
        snaps = data.get("snapshots") if isinstance(data, dict) else None
        if not isinstance(snaps, dict):
            return {}
        out: dict[str, CoinGeckoMarketOverview] = {}
        for sym, body in snaps.items():
            if not isinstance(body, dict):
                continue
            try:
                out[str(sym)] = CoinGeckoMarketOverview(
                    symbol=str(body["symbol"]),
                    timestamp_utc=str(body["timestamp_utc"]),
                    market_cap_rank=_as_opt_int(body.get("market_cap_rank")),
                    market_cap=_as_opt_float(body.get("market_cap")),
                    price_change_pct_30d=_as_opt_float(body.get("price_change_pct_30d")),
                    source=str(body.get("source", "coingecko")),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def read(self, symbol: str) -> CoinGeckoMarketOverview | None:
        return self.read_all().get(symbol.strip().upper())


def append_overview_shadow_log(
    path: Path | str, *, overview: CoinGeckoMarketOverview
) -> None:
    """Append-only read-only Mess-Spur für Overview-Beiträge.

    Fail-safe: Schreibfehler werden geloggt + verschluckt (die Mess-Spur darf
    nie einen Aufrufer killen).
    """
    record = {"ts": datetime.now(UTC).isoformat(), **asdict(overview)}
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — Mess-Spur darf Aufrufer nie killen
        logger.warning("[cg-overview-shadow] append failed: %s", exc)


def _as_opt_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _as_opt_int(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CoinGeckoMarketOverview",
    "CoinGeckoOverviewSettings",
    "CoinGeckoOverviewStore",
    "append_overview_shadow_log",
]
