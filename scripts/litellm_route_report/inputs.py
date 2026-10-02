"""Die drei Eingaben lesen: Telemetrie, Eval-Report, Transport-Log.

Tolerant, wo Toleranz nichts erfindet: eine defekte Telemetriezeile wird
gezaehlt und verworfen, nicht repariert. Eine Eingabe, die gar nicht lesbar ist
oder ein fremdes Format hat, bricht den Lauf ab -- ein Bericht ueber eine Datei,
die niemand lesen konnte, saehe aus wie ein Bericht ueber eine leere.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from scripts.litellm_route_report.models import (
    PURPOSE_ROUTE,
    CallRecord,
    EvalReportSummary,
    TransportLogSummary,
    TransportVerification,
)
from scripts.litellm_shadow_eval.loader import input_label

#: Welche Telemetriezeilen der Bericht annimmt -- eine Aufzaehlung, kein
#: Praefixvergleich. Bumpt der Schreiber (``app.observability.llm_telemetry.
#: SCHEMA_VERSION``), schlaegt der Vertragstest an, und jemand entscheidet
#: bewusst, statt dass eine v9-Zeile still mitgelesen wird.
KNOWN_TELEMETRY_SCHEMAS: Final = frozenset(
    {"v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10"}
)

#: Die Berichtsformate von ``scripts/litellm_shadow_eval``. v2 bringt die
#: Laufzeitnachweise als Objekte mit; v1 hat keine.
EVAL_REPORT_SCHEMAS: Final = frozenset(
    {"litellm-shadow-eval-report/v1", "litellm-shadow-eval-report/v2"}
)
_PROOF_KEYS: Final = ("proven", "referenced", "artifact", "artifact_sha256", "proven_at", "version")

#: Die Zeile, die ``scripts/pi_transport_exec.sh`` vor dem ``exec`` schreibt:
#: ``TRANSPORT_VERIFIED name=<n> version=<v> tree=<pfad> manifest=<16 hex>``.
TRANSPORT_MARKER: Final = "TRANSPORT_VERIFIED"
_TRANSPORT_KEYS: Final = frozenset({"name", "version", "tree", "manifest"})

#: logrotate auf der Pi (``deploy/logrotate/kai``): taeglich, ``rotate 14``,
#: ``delaycompress`` -- ``.1`` liegt im Klartext, aeltere als ``.N.gz``. Die
#: Beleg-Zeile entsteht nur beim Start des Transports; nach der ersten Mitternacht
#: steht sie also NICHT mehr in der aktuellen Datei.
ROTATION_DEPTH: Final = 14

UNKNOWN_TRANSPORT: Final = "unbekannt"


class InputError(ValueError):
    """Eine Eingabe ist nicht lesbar oder hat ein fremdes Format."""


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _commit(value: object) -> str | None:
    """Ein voller Commit (40 hex) -- alles andere ist kein Beleg ueber ein Release."""
    text = _text(value)
    if text is None:
        return None
    text = text.lower()
    return text if len(text) == 40 and all(c in "0123456789abcdef" for c in text) else None


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def aware_timestamp(value: object) -> datetime | None:
    """ISO-8601 MIT Zeitzone, sonst ``None``. Naive Zeit zaehlt nicht."""
    text = _text(value)
    if text is None:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(UTC)


def chain_level(value: object) -> int:
    """Kettenebene wie ``app.ai.spend.chain_position``: ``-1`` im Zweifel."""
    if not isinstance(value, (int, float, str)):
        return -1
    try:
        return int(value)
    except (ValueError, OverflowError):
        return -1


def _usage_reported(raw: dict[str, Any]) -> bool:
    """Hat die Zeile Verbrauch gemeldet? Das v2-Feld gilt, sobald es DA ist.

    ``prompt_tokens``/``completion_tokens`` (v1) tragen 0 auch fuer "nicht
    gezaehlt"; sie zaehlen nur, wo das v2-Feld fehlt -- dieselbe Regel wie im
    Shadow-Auswerter.
    """
    for v2, v1 in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
        tokens = _integer(raw.get(v2)) if v2 in raw else _integer(raw.get(v1))
        if tokens:
            return True
    return False


def _pair(provider: object, model: object) -> str | None:
    anbieter, modell = _text(provider), _text(model)
    return f"{anbieter}/{modell}" if anbieter and modell else None


def normalize_row(raw: dict[str, Any]) -> tuple[CallRecord | None, str | None]:
    """Eine Zeile oder ein Grund, warum sie nicht zaehlt."""
    if _text(raw.get("schema_version")) not in KNOWN_TELEMETRY_SCHEMAS:
        return None, "UNBEKANNTE_SCHEMA_VERSION"
    ts = aware_timestamp(raw.get("ts"))
    if ts is None:
        return None, "ZEITSTEMPEL_UNGUELTIG"
    ok = raw.get("ok")
    if not isinstance(ok, bool):
        return None, "OK_FEHLT"

    route = _text(raw.get("logical_route"))
    from_purpose = False
    if route is None:
        route = PURPOSE_ROUTE.get(_text(raw.get("purpose")) or "")
        from_purpose = route is not None

    outcome = _text(raw.get("outcome"))
    actual = _pair(raw.get("actual_provider"), raw.get("actual_model"))
    # Ein `identity_proven=true` ohne Anbieter UND Modell belegt nichts.
    proven = raw.get("identity_proven") is True and actual is not None

    retry_count = _integer(raw.get("retry_count"))
    attempt = _integer(raw.get("attempt"))
    if retry_count is None and attempt is not None and attempt >= 1:
        retry_count = attempt - 1
    if retry_count is not None and retry_count < 0:
        retry_count = None

    cost = _number(raw.get("cost_usd"))
    # Eine Zahl neben `cost_known=false` ist eine erfundene Zahl.
    if cost is not None and (cost < 0 or raw.get("cost_known") is False):
        cost = None

    return (
        CallRecord(
            ts=ts,
            route=route,
            route_from_purpose=from_purpose,
            transport=(_text(raw.get("transport")) or UNKNOWN_TRANSPORT).lower(),
            mode=_text(raw.get("mode")),
            ok=ok,
            success=ok and outcome in (None, "success"),
            truncated=raw.get("truncated") is True,
            correlation_id=_text(raw.get("correlation_id")),
            chain_position=chain_level(raw.get("chain_position", -1)),
            identity_proven=proven,
            actual=actual if proven else None,
            claimed=None
            if proven
            else _pair(raw.get("provider"), raw.get("model"))
            or _text(raw.get("requested_model_alias")),
            error_class=_text(raw.get("error_class")),
            retry_count=retry_count,
            transport_retries=_integer(raw.get("transport_retries")),
            fallback_known="fallback_from" in raw or "fallback_to" in raw,
            fallback_used=bool(_text(raw.get("fallback_from")) or _text(raw.get("fallback_to"))),
            circuit_state=_text(raw.get("circuit_state")),
            cost_usd=cost,
            cost_source=_text(raw.get("cost_source")) if cost is not None else None,
            usage_reported=_usage_reported(raw),
            runtime_commit=_commit(raw.get("runtime_commit")),
        ),
        None,
    )


@dataclass(frozen=True, slots=True)
class LoadedTelemetry:
    records: tuple[CallRecord, ...]
    files: dict[str, str]
    rows_read: int
    issues: dict[str, int]
    schema_versions: dict[str, int]


def load_telemetry(paths: list[Path]) -> LoadedTelemetry:
    """JSONL zeilenweise; behalten wird nur der reduzierte Datensatz."""
    records: list[CallRecord] = []
    files: dict[str, str] = {}
    issues: Counter[str] = Counter()
    versions: Counter[str] = Counter()
    rows_read = 0
    for index, path in enumerate(sorted(paths, key=str), start=1):
        # Etikett statt Pfad: der Pfad des Operators gehoert nicht in einen
        # Bericht, der weitergereicht wird.
        label = input_label(path, index)
        digest = hashlib.sha256()
        try:
            handle = path.open("rb")
        except OSError as exc:
            raise InputError(f"Telemetrie nicht lesbar: {label} ({type(exc).__name__})") from exc
        with handle:
            for raw_line in handle:
                digest.update(raw_line)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                rows_read += 1
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    issues["JSON_DEFEKT"] += 1
                    continue
                if not isinstance(parsed, dict):
                    issues["KEIN_OBJEKT"] += 1
                    continue
                versions[_text(parsed.get("schema_version")) or "fehlt"] += 1
                record, issue = normalize_row(parsed)
                if record is None:
                    issues[issue or "UNBEKANNT"] += 1
                    continue
                records.append(record)
        files[label] = digest.hexdigest()
    return LoadedTelemetry(
        records=tuple(records),
        files=files,
        rows_read=rows_read,
        issues=dict(sorted(issues.items())),
        schema_versions=dict(sorted(versions.items())),
    )


def _read_bytes(path: Path, what: str) -> tuple[bytes, str]:
    label = input_label(path, 1)
    try:
        return path.read_bytes(), label
    except OSError as exc:
        raise InputError(f"{what} nicht lesbar: {label} ({type(exc).__name__})") from exc


def _hours(later: datetime, earlier: datetime) -> float:
    return round((later - earlier).total_seconds() / 3600.0, 2)


def _decision(entry: dict[str, Any]) -> dict[str, Any]:
    reasons = entry.get("reasons")
    ready = entry.get("primary_ready")
    return {
        "status": _text(entry.get("status")),
        "reasons": [item for item in reasons if isinstance(item, str)]
        if isinstance(reasons, list)
        else [],
        "primary_ready": ready if isinstance(ready, bool) else None,
    }


def _runtime_evidence(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    value: dict[str, Any] = {}
    for name, entry in sorted(raw.items()):
        if not isinstance(name, str):
            continue
        if isinstance(entry, dict):
            value[name] = {key: entry.get(key) for key in _PROOF_KEYS}
        elif isinstance(entry, bool):
            value[name] = entry
    return value


def load_eval_report(path: Path, now: datetime) -> EvalReportSummary:
    """Den Report des Freigabewerkzeugs UEBERNEHMEN -- nicht neu bewerten."""
    data, label = _read_bytes(path, "Eval-Report")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"Eval-Report ist kein JSON: {label}") from exc
    if not isinstance(value, dict):
        raise InputError(f"Eval-Report ist kein JSON-Objekt: {label}")
    schema = _text(value.get("schema_version"))
    if schema not in EVAL_REPORT_SCHEMAS:
        raise InputError(f"Eval-Report-Schema nicht unterstuetzt: {schema!r}")

    reasons: dict[str, str] = {}
    generated_at = aware_timestamp(value.get("generated_at"))
    if generated_at is None:
        reasons["generated_at"] = "Report nennt keinen Erzeugungszeitpunkt mit Zeitzone"
        reasons["age_hours"] = "ohne Erzeugungszeitpunkt kein Alter"
    for key in ("tool_version", "policy_hash", "input_sha256"):
        if _text(value.get(key)) is None:
            reasons[key] = f"Report nennt kein {key}"

    raw_ready = value.get("primary_ready_routes")
    ready = (
        sorted(item for item in raw_ready if isinstance(item, str))
        if isinstance(raw_ready, list)
        else None
    )
    if ready is None:
        reasons["primary_ready_routes"] = "Report nennt keine primary_ready_routes"

    raw_decisions = value.get("decisions")
    decisions = (
        {
            route: _decision(entry)
            for route, entry in sorted(raw_decisions.items())
            if isinstance(route, str) and isinstance(entry, dict)
        }
        if isinstance(raw_decisions, dict)
        else {}
    )

    runtime = _runtime_evidence(value.get("runtime_evidence"))
    if runtime is None:
        reasons["runtime_evidence"] = (
            "v1-Report fuehrt keine Laufzeitnachweise"
            if schema.endswith("/v1")
            else "Report enthaelt kein runtime_evidence-Objekt"
        )
    return EvalReportSummary(
        label=label,
        sha256=hashlib.sha256(data).hexdigest(),
        schema_version=schema,
        tool_version=_text(value.get("tool_version")),
        generated_at=generated_at.isoformat() if generated_at else None,
        age_hours=_hours(now, generated_at) if generated_at else None,
        policy_hash=_text(value.get("policy_hash")),
        input_sha256=_text(value.get("input_sha256")),
        primary_ready_routes=ready,
        decisions=decisions,
        runtime_evidence=runtime,
        null_reasons=reasons,
    )


def _verification(line: str, position: int) -> TransportVerification:
    fields: dict[str, str] = {}
    for token in line[position + len(TRANSPORT_MARKER) :].split():
        key, separator, value = token.partition("=")
        if separator and key in _TRANSPORT_KEYS and value:
            fields[key] = value
    # Seit dem Nachtrag zum LiteLLM-Audit (30.09.) stellt `pi_transport_exec.sh`
    # einen UTC-Zeitstempel voran. Aeltere Zeilen tragen keinen; systemd haengt
    # per `StandardError=append:` auch keinen an.
    prefix = line[:position].split()
    moment = aware_timestamp(prefix[0]) if prefix else None
    reasons: dict[str, str] = {}
    if moment is None:
        reasons["verified_at"] = (
            "Zeile traegt keinen Zeitstempel (vor dem Nachtrag vom 30.09. geschrieben)"
        )
    for key in ("version", "tree", "manifest"):
        if key not in fields:
            reasons[key] = f"Zeile nennt kein {key}="
    return TransportVerification(
        name=fields.get("name", UNKNOWN_TRANSPORT),
        version=fields.get("version"),
        tree=fields.get("tree"),
        manifest=fields.get("manifest"),
        verified_at=moment.isoformat() if moment else None,
        null_reasons=reasons,
    )


def _rotations(path: Path) -> list[Path]:
    """Die vorhandenen Rotationen von ``path``, neueste zuerst."""
    kandidaten: list[Path] = []
    for nummer in range(1, ROTATION_DEPTH + 1):
        for name in (f"{path.name}.{nummer}", f"{path.name}.{nummer}.gz"):
            kandidat = path.with_name(name)
            if kandidat.is_file():
                kandidaten.append(kandidat)
    return kandidaten


def _scan_transport_log(data: bytes) -> tuple[int, dict[str, TransportVerification]]:
    last: dict[str, TransportVerification] = {}
    count = 0
    for line in data.decode("utf-8", errors="replace").splitlines():
        position = line.find(TRANSPORT_MARKER)
        if position < 0:
            continue
        count += 1
        verification = _verification(line, position)
        last[verification.name] = verification
    return count, last


def load_transport_log(path: Path) -> TransportLogSummary:
    """Die letzte ``TRANSPORT_VERIFIED``-Zeile je Transport.

    Traegt die aktuelle Datei keine, gilt die juengste Rotation, die eine traegt.
    ``label`` und ``sha256`` nennen dann genau diese Datei.
    """
    data, label = _read_bytes(path, "Transport-Log")
    count, last = _scan_transport_log(data)
    if count == 0:
        for rotiert in _rotations(path):
            try:
                roh = rotiert.read_bytes()
                text = gzip.decompress(roh) if rotiert.suffix == ".gz" else roh
            except (OSError, EOFError, gzip.BadGzipFile):
                continue
            gefunden, letzte = _scan_transport_log(text)
            if gefunden:
                data, label, count, last = roh, input_label(rotiert, 1), gefunden, letzte
                break
    return TransportLogSummary(
        label=label,
        sha256=hashlib.sha256(data).hexdigest(),
        verified_lines=count,
        last=dict(sorted(last.items())),
    )


__all__ = [
    "EVAL_REPORT_SCHEMAS",
    "KNOWN_TELEMETRY_SCHEMAS",
    "TRANSPORT_MARKER",
    "UNKNOWN_TRANSPORT",
    "InputError",
    "LoadedTelemetry",
    "aware_timestamp",
    "chain_level",
    "load_eval_report",
    "load_telemetry",
    "load_transport_log",
    "normalize_row",
]
