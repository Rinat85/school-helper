"""Миграции: живую базу нельзя пересоздать — её можно только аккуратно менять."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from schoolhelper.storage import db, migrations

SCHEMA = Path(db.__file__).with_name("schema.sql")


def _legacy_db(tmp_path) -> sqlite3.Connection:
    """База «как на сервере до миграций»: только исходная схема и живой человек."""
    conn = sqlite3.connect(tmp_path / "legacy.db", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO klass (id, name, created_at) VALUES (1, '1 «В»', '2026-09-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO person (class_id, display_name, joined_at) "
        "VALUES (1, 'Анна', '2026-09-02T00:00:00Z')"
    )
    return conn


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_existing_people_stay_members(tmp_path):
    """Кто был в классе до правила об одобрении — одобрен задним числом."""
    conn = _legacy_db(tmp_path)
    assert "approved_at" not in _columns(conn, "person")

    assert migrations.apply(conn) == [1]

    row = conn.execute("SELECT approved_at, joined_at FROM person").fetchone()
    assert row["approved_at"] == row["joined_at"]


def test_second_run_changes_nothing(tmp_path):
    conn = _legacy_db(tmp_path)
    migrations.apply(conn)
    assert migrations.apply(conn) == []
    assert migrations.version(conn) == 1


def test_fresh_database_is_fully_migrated(fresh_db):
    conn = db.connect()
    assert {"approved_at", "approved_by"} <= _columns(conn, "person")
    assert migrations.version(conn) == len(migrations.MIGRATIONS)


def test_failed_migration_leaves_no_trace(tmp_path, monkeypatch):
    """Упавшая миграция откатывается целиком: полуизменённой базы не бывает."""
    conn = _legacy_db(tmp_path)
    migrations.apply(conn)

    def broken(c):
        c.execute("CREATE TABLE half_done (x INTEGER)")
        raise RuntimeError("упала посередине")

    extended = [*migrations.MIGRATIONS, (99, "сломанная", broken)]
    monkeypatch.setattr(migrations, "MIGRATIONS", extended)
    with pytest.raises(RuntimeError):
        migrations.apply(conn)

    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "half_done" not in tables
    assert migrations.version(conn) == 1
