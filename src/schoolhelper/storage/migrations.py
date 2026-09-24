"""Миграции схемы.

schema.sql — замороженная исходная схема (все CREATE ... IF NOT EXISTS).
Её больше не правят: на живой базе CREATE IF NOT EXISTS не добавит новую
колонку в существующую таблицу. Любое изменение — новая миграция здесь.

Правила:
  * номер только растёт, выполненные миграции не редактируются;
  * каждая миграция — в своей транзакции: либо целиком, либо никак;
  * миграция должна переживать повторный запуск (проверять, что уже сделано);
  * менять CHECK или тип колонки в SQLite нельзя без пересоздания таблицы —
    поэтому вместо смены значений в CHECK добавляем новые колонки.

Снимок базы перед каждой выкаткой делает deploy.sh.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from ..core import logger, util

log = logger.get(__name__)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _m1_person_approval(conn: sqlite3.Connection) -> None:
    """Новый участник по ссылке ждёт одобрения председателя.

    Ссылку-приглашение пересылают, и без одобрения в класс мог войти кто угодно.
    Все, кто уже в базе, считаются одобренными — они вошли до этого правила.
    """
    if "approved_at" not in _columns(conn, "person"):
        conn.execute("ALTER TABLE person ADD COLUMN approved_at TEXT")
        conn.execute("ALTER TABLE person ADD COLUMN approved_by INTEGER REFERENCES person(id)")
    conn.execute("UPDATE person SET approved_at = joined_at WHERE approved_at IS NULL")


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "person: одобрение участия председателем", _m1_person_approval),
]


def apply(conn: sqlite3.Connection) -> list[int]:
    """Выполняет недостающие миграции. Возвращает номера выполненных."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration ("
        " id INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    done = {row["id"] for row in conn.execute("SELECT id FROM schema_migration")}

    applied: list[int] = []
    for number, name, migrate in MIGRATIONS:
        if number in done:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            migrate(conn)
            conn.execute(
                "INSERT INTO schema_migration (id, name, applied_at) VALUES (?, ?, ?)",
                (number, name, util.now_iso()),
            )
        except Exception:
            conn.execute("ROLLBACK")
            log.exception("migration %s failed, rolled back", number)
            raise
        conn.execute("COMMIT")
        log.info("migration %s applied: %s", number, name)
        applied.append(number)
    return applied


def version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(id) FROM schema_migration").fetchone()
    return int(row[0] or 0)
