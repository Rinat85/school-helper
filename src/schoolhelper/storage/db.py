"""Доступ к SQLite.

Соединение — по одному на поток (WAL допускает много читателей + одного писателя).
На нашем объёме (десятки человек, единицы запросов в секунду) синхронный sqlite3
внутри async-приложения дешевле любого пула: запрос отрабатывает за доли миллисекунды.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import config

_local = threading.local()
_SCHEMA = Path(__file__).with_name("schema.sql")


def connect() -> sqlite3.Connection:
    """Соединение текущего потока. Создаёт файл БД, если его нет."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn

    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    _local.conn = conn
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Транзакция. Любая денежная мутация должна идти через неё."""
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def query(sql: str, *params: Any) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def one(sql: str, *params: Any) -> sqlite3.Row | None:
    return connect().execute(sql, params).fetchone()


def scalar(sql: str, *params: Any) -> Any:
    row = one(sql, *params)
    return row[0] if row is not None else None


def execute(sql: str, *params: Any) -> sqlite3.Cursor:
    return connect().execute(sql, params)


def insert(table: str, **values: Any) -> int:
    """INSERT со словарём полей, возвращает id."""
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = connect().execute(
        f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values())
    )
    return int(cur.lastrowid)


def migrate() -> None:
    """Применяет schema.sql. Идемпотентно — все CREATE идут с IF NOT EXISTS."""
    conn = connect()
    conn.executescript(_SCHEMA.read_text(encoding="utf-8"))


def get_meta(key: str, default: str | None = None) -> str | None:
    row = one("SELECT value FROM meta WHERE key = ?", key)
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        key,
        value,
    )
