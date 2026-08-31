"""Read-only consumer for the two G5 input-contract rejection streams."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.analysis.input_contract import ANALYSIS_INPUT_REJECTIONS_FILENAME
from app.lightning.input_contract_rejections import LN_INPUT_REJECTIONS_FILENAME

DEFAULT_LN_INPUT_REJECTIONS_PATH = Path("artifacts") / LN_INPUT_REJECTIONS_FILENAME
DEFAULT_ANALYSIS_INPUT_REJECTIONS_PATH = Path("artifacts") / ANALYSIS_INPUT_REJECTIONS_FILENAME

_SAFE_FIELDS = {
    LN_INPUT_REJECTIONS_FILENAME: frozenset({"schema", "ts", "contract", "action", "reasons"}),
    ANALYSIS_INPUT_REJECTIONS_FILENAME: frozenset(
        {
            "schema",
            "ts",
            "contract",
            "document_id",
            "source_type",
            "reason",
            "content_chars",
            "minimum_chars",
        }
    ),
}

_EXPECTED_SCHEMAS = {
    LN_INPUT_REJECTIONS_FILENAME: "money-input-rejection/v1",
    ANALYSIS_INPUT_REJECTIONS_FILENAME: "analysis-input-rejection/v1",
}


@dataclass(frozen=True)
class InputRejectionStreamProblem:
    """A concrete readability or schema problem in an existing reject stream."""

    stream: str
    detail: str


def _read_recent(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not path.is_file():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return list(rows)


def read_recent_input_rejections(
    *,
    ln_path: Path = DEFAULT_LN_INPUT_REJECTIONS_PATH,
    analysis_path: Path = DEFAULT_ANALYSIS_INPUT_REJECTIONS_PATH,
    limit: int = 100,
) -> list[dict[str, object]]:
    """Return recent raw rejects from both streams, oldest first.

    No aggregate is produced: operators see the actual reason records and can
    decide whether a caller, a source, or the contract itself needs attention.
    """
    if limit <= 0:
        return []
    tagged: list[dict[str, object]] = []
    for stream, path in (
        (LN_INPUT_REJECTIONS_FILENAME, ln_path),
        (ANALYSIS_INPUT_REJECTIONS_FILENAME, analysis_path),
    ):
        tagged.extend(
            {
                "stream": stream,
                "record": {
                    key: value for key, value in record.items() if key in _SAFE_FIELDS[stream]
                },
            }
            for record in _read_recent(path, limit=limit)
        )
    tagged.sort(key=lambda row: str(cast_record(row).get("ts", "")))
    return tagged[-limit:]


def inspect_input_rejection_streams(
    *,
    ln_path: Path = DEFAULT_LN_INPUT_REJECTIONS_PATH,
    analysis_path: Path = DEFAULT_ANALYSIS_INPUT_REJECTIONS_PATH,
    tail_records: int = 100,
) -> list[InputRejectionStreamProblem]:
    """Validate recent records without treating legitimate silence as staleness.

    These streams only write when an input is rejected. A missing file is
    therefore healthy. Writer failures are surfaced synchronously at the
    rejecting boundary; this probe catches a stream that exists but has become
    unreadable or contains malformed recent records.
    """
    problems: list[InputRejectionStreamProblem] = []
    for stream, path in (
        (LN_INPUT_REJECTIONS_FILENAME, ln_path),
        (ANALYSIS_INPUT_REJECTIONS_FILENAME, analysis_path),
    ):
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                raw_rows = deque((line for line in handle if line.strip()), maxlen=tail_records)
        except OSError as exc:
            problems.append(
                InputRejectionStreamProblem(
                    stream=stream, detail=f"unreadable: {type(exc).__name__}"
                )
            )
            continue

        expected_schema = _EXPECTED_SCHEMAS[stream]
        for offset, raw in enumerate(raw_rows, start=1):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                problems.append(
                    InputRejectionStreamProblem(
                        stream=stream,
                        detail=f"malformed JSON in recent record {offset}",
                    )
                )
                continue
            if not isinstance(value, dict) or value.get("schema") != expected_schema:
                problems.append(
                    InputRejectionStreamProblem(
                        stream=stream,
                        detail=f"unexpected schema in recent record {offset}",
                    )
                )
    return problems


def cast_record(row: dict[str, object]) -> dict[str, Any]:
    """Narrow the internal tagged-row shape without trusting JSON contents."""
    record = row.get("record")
    return record if isinstance(record, dict) else {}


__all__ = [
    "ANALYSIS_INPUT_REJECTIONS_FILENAME",
    "DEFAULT_ANALYSIS_INPUT_REJECTIONS_PATH",
    "DEFAULT_LN_INPUT_REJECTIONS_PATH",
    "InputRejectionStreamProblem",
    "LN_INPUT_REJECTIONS_FILENAME",
    "inspect_input_rejection_streams",
    "read_recent_input_rejections",
]
