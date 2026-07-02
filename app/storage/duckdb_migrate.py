"""DuckDB Schema-Migration-Tool.

ADR 0003 (2026-05-09): explicit DDL + versioned migrations für DuckDB —
Pendant zu Alembic für PostgreSQL. Migrationen liegen als nummerierte
``.sql``-Files in ``app/storage/duckdb_migrations/``. Jede Migration
ist idempotent (CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
INSERT OR IGNORE). Tracking erfolgt über die ``_schema_versions``-Tabelle,
die vom 0001-Initial-Schema selbst angelegt wird.

CLI-Use:
    python -m app.storage.duckdb_migrate --db-path artifacts/analytics.duckdb
    python -m app.storage.duckdb_migrate --db-path artifacts/analytics.duckdb --check-only

Library-Use:
    from app.storage.duckdb_migrate import apply_migrations, current_schema_version
    with duckdb.connect(db_path) as con:
        applied = apply_migrations(con)
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "duckdb_migrations"
_VERSION_RE = re.compile(r"^(\d{4})_.+\.sql$")


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    description: str

    @property
    def sort_key(self) -> int:
        return int(self.version)


def discover_migrations(migrations_dir: Path = _MIGRATIONS_DIR) -> list[Migration]:
    """Find all migrations sorted by version."""
    if not migrations_dir.exists():
        return []
    migrations: list[Migration] = []
    for sql_file in migrations_dir.glob("*.sql"):
        match = _VERSION_RE.match(sql_file.name)
        if not match:
            logger.warning("Skipping non-conformant migration filename: %s", sql_file.name)
            continue
        version = match.group(1)
        # Description from first SQL comment line if present
        description = sql_file.stem[len(version) + 1 :]  # strip "0001_"
        migrations.append(Migration(version=version, path=sql_file, description=description))
    migrations.sort(key=lambda m: m.sort_key)
    return migrations


def _ensure_version_table(con: duckdb.DuckDBPyConnection) -> None:
    """Create _schema_versions table if missing (bootstrap-only).

    The 0001-migration creates this table itself; this guard supports
    pre-0001 runs (fresh DB without any migrations applied yet).
    """
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_versions (
            version       VARCHAR PRIMARY KEY,
            applied_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            description   VARCHAR NOT NULL,
            applied_by    VARCHAR
        )
        """
    )


def applied_versions(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Return set of versions already applied to this DB."""
    _ensure_version_table(con)
    rows = con.execute("SELECT version FROM _schema_versions").fetchall()
    return {str(row[0]) for row in rows}


def current_schema_version(con: duckdb.DuckDBPyConnection) -> str | None:
    """Return the highest applied version, or None if none."""
    versions = applied_versions(con)
    if not versions:
        return None
    return max(versions, key=lambda v: int(v))


def apply_migrations(
    con: duckdb.DuckDBPyConnection,
    *,
    migrations_dir: Path = _MIGRATIONS_DIR,
    target_version: str | None = None,
) -> list[str]:
    """Apply pending migrations in order. Returns list of applied versions.

    Idempotent: re-running this on an up-to-date DB returns empty list.
    Each migration runs in DuckDB's auto-commit mode; if a migration's SQL
    fails mid-file, the partial state is committed for already-completed
    statements. Migrations should therefore use IF NOT EXISTS / INSERT OR
    IGNORE for re-runnability.

    Args:
        con: open DuckDB connection (must be writable).
        migrations_dir: directory holding NNNN_*.sql files.
        target_version: stop after applying this version (inclusive). None = apply all.

    Returns:
        List of newly-applied version strings, in order applied.
    """
    _ensure_version_table(con)
    already = applied_versions(con)
    pending = [m for m in discover_migrations(migrations_dir) if m.version not in already]
    if target_version is not None:
        pending = [m for m in pending if int(m.version) <= int(target_version)]

    applied: list[str] = []
    for migration in pending:
        sql_text = migration.path.read_text(encoding="utf-8")
        logger.info(
            "Applying migration %s: %s (%d bytes)",
            migration.version,
            migration.description,
            len(sql_text),
        )
        try:
            con.execute(sql_text)
        except duckdb.Error as exc:  # pragma: no cover - re-raise with context
            logger.error(
                "Migration %s FAILED: %s. Partial state may be committed.",
                migration.version,
                exc,
            )
            raise
        # Belt-and-suspenders: even if the migration's INSERT INTO _schema_versions
        # didn't run (e.g. older migration files), record it ourselves.
        con.execute(
            "INSERT OR IGNORE INTO _schema_versions(version, description, applied_by) "
            "VALUES (?, ?, ?)",
            [migration.version, migration.description, "duckdb_migrate"],
        )
        applied.append(migration.version)
        logger.info("Migration %s applied OK", migration.version)
    return applied


def check_pending(
    con: duckdb.DuckDBPyConnection,
    *,
    migrations_dir: Path = _MIGRATIONS_DIR,
) -> list[str]:
    """Return list of pending (un-applied) migration versions."""
    _ensure_version_table(con)
    already = applied_versions(con)
    return [m.version for m in discover_migrations(migrations_dir) if m.version not in already]


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply DuckDB schema migrations.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("artifacts/analytics.duckdb"),
        help="Path to DuckDB file (default: artifacts/analytics.duckdb)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Print pending migrations without applying.",
    )
    parser.add_argument(
        "--target-version",
        type=str,
        default=None,
        help="Stop after applying this version (e.g. '0001').",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    args.db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(args.db_path)) as con:
        if args.check_only:
            pending = check_pending(con)
            current = current_schema_version(con)
            print(f"Current schema version: {current or '(none)'}")
            if pending:
                print(f"Pending migrations ({len(pending)}): {', '.join(pending)}")
                return 1
            print("All migrations applied.")
            return 0

        applied = apply_migrations(con, target_version=args.target_version)
        if applied:
            print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
        else:
            print("No pending migrations.")
        current = current_schema_version(con)
        print(f"Current schema version: {current or '(none)'}")
        return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
