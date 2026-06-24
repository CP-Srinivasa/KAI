"""Settings der orthogonalen Bayes-Evidence-Schichten (Goal V5 + HYPE-S1).

Sprint-S7-Extraktion (D-234): diese Klassen lebten als zusammenhängender
Block in ``app/core/settings.py`` (God-File, Ratchet). Der Block ist das
„berührte Segment" von HYPE-S1 und wandert deshalb als Ganzes hierher —
``app/core/settings.py`` re-exportiert die Namen, bestehende Importe bleiben
byte-für-byte gültig (gleiche env-Prefixe, gleiche Defaults, kein
Verhaltenswechsel).

Gemeinsamer Vertrag aller Evidence-Settings (V5-Disziplin):

  - ``enabled=False`` (default): kein Provider wird verdrahtet → der
    SignalGenerator läuft exakt wie ohne die Schicht. Ein frisches Deployment
    ändert NICHTS, bis der Operator bewusst opt-in macht.
  - ``enabled=True``: der Loop liest ausschließlich den *warmen* Snapshot von
    Platte (geschrieben von einem entkoppelten Refresh-Service). KEIN
    Inline-Netz-I/O im Loop.
  - ``source_trust`` konservativ 0.5: die Evidence soll die Confidence
    anfangs nur leicht verschieben. Anhebung erst nach Shadow-Log-Auswertung
    + Operator-Sign-off.
  - ``ttl_seconds`` gated stale Snapshots aus — fail-safe, wenn der
    Refresh-Service ausfällt (keine Evidence auf toten Daten).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FundingEvidenceSettings(BaseSettings):
    """Goal V5 Phase 1 — Funding-Rate als orthogonale Signal-Evidence.

    Default-off, measure-first — spiegelt die Bayes/Diversification-Rollout-
    Disziplin: ein frisches Deployment ändert NICHTS am Verhalten, bis der
    Operator bewusst opt-in macht.

      - ``enabled=False`` (default): ``build_bayes_signal_kwargs`` bekommt
        KEINEN Funding-Provider → SignalGenerator läuft exakt wie vorher.
        Kein Refresh-Service, kein Cache, kein Verhaltenswechsel.
      - ``enabled=True``: der Loop liest beim Bauen den *warmen* Funding-
        Snapshot von Platte (``snapshot_path``, geschrieben vom entkoppelten
        ``funding_cache_refresh``-Service) und verdrahtet den Provider.
        Inline-Netz-I/O im Loop findet NICHT statt — nur ein Disk-Read.

    ``source_trust`` ist bewusst konservativ (0.5 default): Funding soll
    die Confidence anfangs nur leicht verschieben, nicht dominieren. Erst
    nach Auswertung des Shadow-Logs (``shadow_log_path``) wird der Trust
    nach Operator-Sign-off angehoben.

    ``ttl_seconds`` (default 1 h) bestimmt, ab wann ein Disk-Snapshot als
    stale gilt und der Provider keine Evidence mehr liefert — fail-safe,
    falls der Refresh-Service ausfällt (kein Trade auf veraltetem Funding).
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_FUNDING_EVIDENCE_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    source_trust: float = Field(default=0.5, ge=0.0, le=1.0)
    ttl_seconds: float = Field(default=3600.0, gt=0.0)
    snapshot_path: Path = Field(default=Path("artifacts/funding_cache.json"))
    shadow_log_path: Path = Field(default=Path("artifacts/funding_evidence_shadow.jsonl"))
    # Per-venue HTTP timeout for the *refresh service* (never the loop).
    refresh_timeout_seconds: float = Field(default=8.0, gt=0.0)


class OpenInterestEvidenceSettings(BaseSettings):
    """Goal V5 Phase 2 — Open-Interest als zweite orthogonale Signal-Evidence.

    Default-off, measure-first — identische Disziplin wie
    ``FundingEvidenceSettings``: ein frisches Deployment ändert NICHTS, bis der
    Operator opt-in macht.

      - ``enabled=False`` (default): kein OI-Provider → SignalGenerator
        unverändert. Kein OI-Refresh, kein Cache, kein Verhaltenswechsel.
      - ``enabled=True``: der Loop liest den *warmen* OI-Snapshot von Platte
        (``snapshot_path``, geschrieben vom entkoppelten OI-Refresh) und
        verdrahtet den Provider. KEIN Inline-Netz-I/O im Loop.

    ``source_trust`` konservativ 0.5: OI soll die Confidence anfangs nur leicht
    verschieben. ``zscore_window`` (Punkte der OI-Serie, default 24 ≈ 24h bei
    1h-Intervall) wird vom Refresh genutzt, NICHT vom Loop — der Loop liest nur
    den fertig berechneten z-score. ``ttl_seconds`` (default 1 h) gated stale
    Snapshots aus (OI-Kadenz ist 1h → 1h TTL ist der natürliche Frische-Rahmen).
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_OI_EVIDENCE_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    source_trust: float = Field(default=0.5, ge=0.0, le=1.0)
    ttl_seconds: float = Field(default=3600.0, gt=0.0)
    snapshot_path: Path = Field(default=Path("artifacts/oi_cache.json"))
    shadow_log_path: Path = Field(default=Path("artifacts/oi_evidence_shadow.jsonl"))
    # Window (number of OI history points) used by the *refresh service* to
    # compute the change-z-score. Never read by the loop.
    zscore_window: int = Field(default=24, ge=3, le=200)
    # OI history bucket interval requested from the venues (refresh only).
    interval: str = Field(default="1h")
    # Per-venue HTTP timeout for the *refresh service* (never the loop).
    refresh_timeout_seconds: float = Field(default=8.0, gt=0.0)


class LongShortRatioEvidenceSettings(BaseSettings):
    """Goal V5 Phase 3 — Long/Short-Account-Ratio als dritte orthogonale Evidence.

    Default-off, measure-first — identische Disziplin wie
    ``FundingEvidenceSettings`` / ``OpenInterestEvidenceSettings``: ein frisches
    Deployment ändert NICHTS, bis der Operator opt-in macht.

      - ``enabled=False`` (default): kein L/S-Provider → SignalGenerator
        unverändert. Kein L/S-Refresh, kein Cache, kein Verhaltenswechsel.
      - ``enabled=True``: der Loop liest den *warmen* L/S-Snapshot von Platte
        (``snapshot_path``, geschrieben vom entkoppelten L/S-Refresh) und
        verdrahtet den Provider. KEIN Inline-Netz-I/O im Loop.

    ``source_trust`` konservativ 0.5: L/S-Account-Ratio ist retail-lastig und
    verrauschter als OI/Funding → soll die Confidence anfangs nur leicht
    verschieben. ``ttl_seconds`` (default 1 h) gated stale Snapshots aus (L/S-
    Buckets sind 1h-Kadenz → 1h TTL ist der natürliche Frische-Rahmen).
    ``interval`` ist die Bucket-Granularität, die der Refresh anfragt (nie der
    Loop).
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_LS_EVIDENCE_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    source_trust: float = Field(default=0.5, ge=0.0, le=1.0)
    ttl_seconds: float = Field(default=3600.0, gt=0.0)
    snapshot_path: Path = Field(default=Path("artifacts/ls_cache.json"))
    shadow_log_path: Path = Field(default=Path("artifacts/ls_evidence_shadow.jsonl"))
    # L/S bucket interval requested from the venues (refresh only).
    interval: str = Field(default="1h")
    # Per-venue HTTP timeout for the *refresh service* (never the loop).
    refresh_timeout_seconds: float = Field(default=8.0, gt=0.0)


class HypeEvidenceSettings(BaseSettings):
    """HYPE-S1 — Sentiment-Überhitzung als vierte orthogonale Signal-Evidence.

    Kernsatz des Moduls: *ein starkes Asset bekommt nicht automatisch ein
    Buy-Signal*. Wenn ein Asset medial überhitzt (abnormale Mention-Velocity,
    breite Quellen-Streuung, einseitiges Sentiment), ist das eine
    contrarian-Warnung GEGEN neue Long-Einstiege — keine Bestätigung.

    Default-off, measure-first — identische Disziplin wie die V5-Schichten:

      - ``enabled=False`` (default): kein Hype-Provider → SignalGenerator
        unverändert. Kein Refresh, kein Verhaltenswechsel.
      - ``enabled=True``: der Loop liest den *warmen* Hype-Snapshot von Platte
        (``snapshot_path``, geschrieben vom entkoppelten
        ``hype_snapshot_refresh``-Service aus den BEREITS VORHANDENEN
        Dokument-/Sentiment-Strömen — keine neue externe Datenquelle).
        KEIN Inline-Netz-I/O und KEIN DB-Zugriff im Loop.

    ``dampen_only=True`` (default, S1-Sicherheitsvertrag): Überhitzung wirkt
    ausschließlich DÄMPFEND auf Long-Signale (contra-Evidence). Sie wird NICHT
    als pro-Short-Evidence gespiegelt — das Modul darf Positionen nur
    verkleinern/verhindern, nie neue begründen. Die symmetrische
    contrarian-Nutzung (analog Funding/LS) ist eine bewusste SPÄTER-Entscheidung
    nach Shadow-Auswertung.

    Scoring-Parameter (werden vom *Refresh* gelesen, nie vom Loop):
      - ``recent_window_hours`` / ``baseline_days``: Hype = Mentions im
        jüngsten Fenster relativ zur eigenen Baseline desselben Assets.
      - ``min_mentions``: unterhalb dieser absoluten Mention-Zahl wird KEIN
        Score gebildet (2 statt 1 Erwähnung ist Rauschen, kein Hype).
      - ``velocity_saturation``: Vielfaches der Baseline, ab dem die
        Velocity-Komponente auf 1.0 sättigt.
      - ``breadth_saturation``: Anzahl distinkter Quellen, ab der die
        Breiten-Komponente auf 1.0 sättigt.

    ``min_score_for_evidence``: erst ab diesem Score wird Evidence in die
    Bayes-Engine gegeben (darunter nur Shadow-Log) — verhindert
    Mikro-Beiträge aus normalem Newsflow.
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_HYPE_EVIDENCE_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    source_trust: float = Field(default=0.5, ge=0.0, le=1.0)
    ttl_seconds: float = Field(default=7200.0, gt=0.0)
    snapshot_path: Path = Field(default=Path("artifacts/hype_cache.json"))
    shadow_log_path: Path = Field(default=Path("artifacts/hype_evidence_shadow.jsonl"))
    dampen_only: bool = Field(default=True)
    min_score_for_evidence: float = Field(default=0.3, ge=0.0, le=1.0)
    # Aggregations-Parameter — nur vom Refresh-Service gelesen, nie vom Loop.
    recent_window_hours: float = Field(default=6.0, gt=0.0, le=48.0)
    baseline_days: float = Field(default=7.0, gt=0.0, le=60.0)
    min_mentions: int = Field(default=5, ge=1, le=1000)
    velocity_saturation: float = Field(default=5.0, gt=1.0, le=100.0)
    breadth_saturation: int = Field(default=5, ge=1, le=100)
    # Obergrenze der DB-Zeilen, die ein Refresh-Lauf lädt (bounded read).
    refresh_max_documents: int = Field(default=20000, ge=100, le=200000)


class L2OnChainEvidenceSettings(BaseSettings):
    """Sprint 2 — L2 On-Chain (Fee/Mempool-Flow) als 5. orthogonale Evidence.

    Default-off, shadow-first, **richtungs-agnostisch** (B-003). Quelle = KAIs
    EIGENE L1-Serie ``artifacts/onchain_fee_shadow.jsonl`` (vom L1 Fee-Truth-
    Scheduler geschrieben) — kein neuer Provider, kein neuer Key.

      - ``enabled=False`` (default): kein L2-Provider → SignalGenerator unverändert.
      - ``enabled=True``: der Provider liest den warmen L1-Stream (Disk-Read, kein
        Netz), berechnet rohe Fee-/Mempool-Percentile gegen das ``window`` und
        schreibt sie in ``shadow_log_path``. Er trägt KEINE vorbestimmte Richtung
        bei (``direction_aligned=0``), bis die Auswertung
        (``scripts/evaluate_l2_evidence.py``) eine Richtung gelernt hat.

    ``source_trust`` konservativ (0.5) — wie V5; Anhebung nur nach Shadow-
    Auswertung + Operator-Sign-off + Edge-Beweis (kein Trust-Promote ohne Beweis).
    ``ttl_seconds``: ist der jüngste Stream-Record älter, liefert der Provider
    keine Features (fail-safe, falls der L1-Scheduler ausfällt).
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_L2_EVIDENCE_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    source_trust: float = Field(default=0.5, ge=0.0, le=1.0)
    ttl_seconds: float = Field(default=3600.0, gt=0.0)
    stream_path: Path = Field(default=Path("artifacts/onchain_fee_shadow.jsonl"))
    shadow_log_path: Path = Field(default=Path("artifacts/l2_evidence_shadow.jsonl"))
    # Recent L1 records used to rank the current fee/mempool (percentile window).
    window: int = Field(default=200, ge=2, le=10000)
    # Minimum history points for a meaningful percentile; below it → no features.
    min_window: int = Field(default=20, ge=2, le=10000)


__all__ = [
    "FundingEvidenceSettings",
    "HypeEvidenceSettings",
    "L2OnChainEvidenceSettings",
    "LongShortRatioEvidenceSettings",
    "OpenInterestEvidenceSettings",
]
