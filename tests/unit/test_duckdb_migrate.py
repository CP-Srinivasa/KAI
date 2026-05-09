"""Tests for DuckDB schema migration tool (ADR 0003 Phase 1)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from app.storage.duckdb_migrate import (
    Migration,
    applied_versions,
    apply_migrations,
    check_pending,
    current_schema_version,
    discover_migrations,
)

# Repo's actual migrations dir — used for end-to-end test against real DDL
_REAL_MIGRATIONS = Path(__file__).resolve().parents[2] / "app" / "storage" / "duckdb_migrations"


def _conn(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(tmp_path / "test.duckdb"))


def test_discover_migrations_sorted_by_version(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0002_second.sql").write_text("-- second", encoding="utf-8")
    (migrations_dir / "0001_first.sql").write_text("-- first", encoding="utf-8")
    (migrations_dir / "0010_tenth.sql").write_text("-- tenth", encoding="utf-8")
    (migrations_dir / "README.md").write_text("ignored", encoding="utf-8")

    migrations = discover_migrations(migrations_dir)
    assert [m.version for m in migrations] == ["0001", "0002", "0010"]


def test_discover_skips_non_conformant_filenames(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_short.sql").write_text("-- bad", encoding="utf-8")  # 3 digits
    (migrations_dir / "abc_nonum.sql").write_text("-- bad", encoding="utf-8")
    (migrations_dir / "0001_good.sql").write_text("-- good", encoding="utf-8")

    migrations = discover_migrations(migrations_dir)
    assert [m.version for m in migrations] == ["0001"]


def test_discover_returns_empty_for_missing_dir() -> None:
    assert discover_migrations(Path("/nonexistent/path")) == []


def test_apply_migrations_creates_version_table_and_applies(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_create_widgets.sql").write_text(
        "CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    with _conn(tmp_path) as con:
        applied = apply_migrations(con, migrations_dir=migrations_dir)
        assert applied == ["0001"]
        # widgets-Tabelle wurde erstellt
        rows = con.execute("SHOW TABLES").fetchall()
        names = {str(row[0]) for row in rows}
        assert "widgets" in names
        assert "_schema_versions" in names
        # Version-Tracking eingetragen
        version_rows = con.execute(
            "SELECT version FROM _schema_versions WHERE version = '0001'"
        ).fetchall()
        assert len(version_rows) == 1


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_create_widgets.sql").write_text(
        "CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    with _conn(tmp_path) as con:
        first = apply_migrations(con, migrations_dir=migrations_dir)
        assert first == ["0001"]
        second = apply_migrations(con, migrations_dir=migrations_dir)
        assert second == []  # nothing pending
        third = apply_migrations(con, migrations_dir=migrations_dir)
        assert third == []


def test_target_version_stops_after_specified(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_a.sql").write_text(
        "CREATE TABLE IF NOT EXISTS a (id INTEGER);", encoding="utf-8"
    )
    (migrations_dir / "0002_b.sql").write_text(
        "CREATE TABLE IF NOT EXISTS b (id INTEGER);", encoding="utf-8"
    )
    (migrations_dir / "0003_c.sql").write_text(
        "CREATE TABLE IF NOT EXISTS c (id INTEGER);", encoding="utf-8"
    )

    with _conn(tmp_path) as con:
        applied = apply_migrations(con, migrations_dir=migrations_dir, target_version="0002")
        assert applied == ["0001", "0002"]
        # 'c' table should NOT exist yet
        rows = con.execute("SHOW TABLES").fetchall()
        names = {str(row[0]) for row in rows}
        assert "a" in names
        assert "b" in names
        assert "c" not in names


def test_current_schema_version_returns_max(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_a.sql").write_text(
        "CREATE TABLE IF NOT EXISTS a (id INTEGER);", encoding="utf-8"
    )
    (migrations_dir / "0010_b.sql").write_text(
        "CREATE TABLE IF NOT EXISTS b (id INTEGER);", encoding="utf-8"
    )

    with _conn(tmp_path) as con:
        assert current_schema_version(con) is None
        apply_migrations(con, migrations_dir=migrations_dir)
        assert current_schema_version(con) == "0010"


def test_check_pending_returns_unapplied(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_a.sql").write_text(
        "CREATE TABLE IF NOT EXISTS a (id INTEGER);", encoding="utf-8"
    )
    (migrations_dir / "0002_b.sql").write_text(
        "CREATE TABLE IF NOT EXISTS b (id INTEGER);", encoding="utf-8"
    )

    with _conn(tmp_path) as con:
        assert check_pending(con, migrations_dir=migrations_dir) == ["0001", "0002"]
        apply_migrations(con, migrations_dir=migrations_dir, target_version="0001")
        assert check_pending(con, migrations_dir=migrations_dir) == ["0002"]


def test_real_migration_0001_applies_cleanly(tmp_path: Path) -> None:
    """End-to-end: das echte 0001_initial_schema.sql aus dem Repo läuft sauber durch."""
    if not _REAL_MIGRATIONS.exists():
        pytest.skip(f"Real migrations dir not found at {_REAL_MIGRATIONS}")

    with _conn(tmp_path) as con:
        applied = apply_migrations(con, migrations_dir=_REAL_MIGRATIONS)
        assert "0001" in applied

        # Verify all 6 tables + 1 view + 2 helper tables exist
        rows = con.execute("SHOW TABLES").fetchall()
        names = {str(row[0]) for row in rows}
        expected_tables = {
            "ticks",
            "signals",
            "trades",
            "metrics",
            "audits",
            "_schema_versions",
            "_compaction_watermark",
        }
        missing = expected_tables - names
        assert not missing, f"Missing tables after migration: {missing}"

        # View pnl_daily should be queryable (even with no data)
        view_rows = con.execute("SELECT COUNT(*) FROM pnl_daily").fetchall()
        assert view_rows[0][0] == 0  # no trades yet


def test_real_migration_idempotent_on_real_schema(tmp_path: Path) -> None:
    """Running 0001 twice on real schema must not error."""
    if not _REAL_MIGRATIONS.exists():
        pytest.skip(f"Real migrations dir not found at {_REAL_MIGRATIONS}")

    with _conn(tmp_path) as con:
        first = apply_migrations(con, migrations_dir=_REAL_MIGRATIONS)
        second = apply_migrations(con, migrations_dir=_REAL_MIGRATIONS)
        assert first == ["0001"]
        assert second == []  # nothing to do


def test_real_migration_indexes_exist(tmp_path: Path) -> None:
    """Verify the Pflichtmetrik-relevant indexes are created."""
    if not _REAL_MIGRATIONS.exists():
        pytest.skip(f"Real migrations dir not found at {_REAL_MIGRATIONS}")

    with _conn(tmp_path) as con:
        apply_migrations(con, migrations_dir=_REAL_MIGRATIONS)
        # DuckDB exposes indexes via duckdb_indexes()
        rows = con.execute(
            "SELECT index_name FROM duckdb_indexes() "
            "WHERE database_name = 'memory' OR database_name LIKE '%test%'"
        ).fetchall()
        index_names = {str(row[0]) for row in rows}
        # Spot-check: the most performance-critical indexes
        critical_indexes = {
            "idx_trades_ts",
            "idx_audits_dispatched",
            "idx_metrics_type_window",
        }
        missing = critical_indexes - index_names
        assert not missing, f"Critical indexes missing: {missing} (found: {index_names})"


def test_applied_versions_returns_set(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_a.sql").write_text(
        "CREATE TABLE IF NOT EXISTS a (id INTEGER);", encoding="utf-8"
    )

    with _conn(tmp_path) as con:
        assert applied_versions(con) == set()
        apply_migrations(con, migrations_dir=migrations_dir)
        assert applied_versions(con) == {"0001"}


def test_migration_dataclass_sort_key() -> None:
    m1 = Migration(version="0001", path=Path("/x.sql"), description="a")
    m10 = Migration(version="0010", path=Path("/y.sql"), description="b")
    m99 = Migration(version="0099", path=Path("/z.sql"), description="c")
    assert m1.sort_key == 1
    assert m10.sort_key == 10
    assert m99.sort_key == 99
    assert sorted([m99, m1, m10], key=lambda m: m.sort_key) == [m1, m10, m99]
