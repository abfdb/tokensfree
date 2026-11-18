"""SQLite connection setup and forward-only migrations.

The ledger is append-only by construction: no migration or repository exposes
an update/delete path for quota_ledger rows.
"""
import sqlite3
import time
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def connect(path=":memory:"):
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def migration_files():
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def migrate(connection):
    """Apply pending migrations in order. Returns the schema version after migrating."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
    )
    connection.commit()
    files = migration_files()
    latest = len(files)
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_version")}
    if applied and max(applied) > latest:
        raise ValueError("database schema is newer than this build")
    for number, path in enumerate(files, start=1):
        if number in applied:
            continue
        connection.executescript(path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)", (number, time.time())
        )
        connection.commit()
    return latest
