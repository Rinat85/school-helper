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


def _m2_class_chats_and_day_schedule(conn: sqlite3.Connection) -> None:
    """У класса три чата, учительница пишет расписание на завтра текстом.

    * class_chat: чаты класса с ролями. Родительский — сборы и приглашения;
      комитет — служебный; чат с учителем — только источник расписания,
      бот туда не пишет ничего. Старая привязка klass.tg_chat_id переезжает
      сюда как родительский чат; сама колонка больше не читается.
    * klass.teacher_*: чьи сообщения в чате с учителем считать расписанием.
      Это не person: учительница не член родительского класса, ей не положены
      ни сборы, ни рассылки.
    * day_schedule: расписание на конкретный день. Черновик — то, что бот
      распознал; опубликованное видят родители. На дату — одно опубликованное.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS class_chat ("
        " id         INTEGER PRIMARY KEY,"
        " class_id   INTEGER NOT NULL REFERENCES klass(id),"
        " tg_chat_id INTEGER NOT NULL UNIQUE,"
        " role       TEXT    NOT NULL CHECK (role IN ('parents', 'committee', 'teacher')),"
        " title      TEXT,"
        # tg_user_id, а не person: будущий председатель из .env может ещё не быть в базе
        " bound_by   INTEGER,"
        " bound_at   TEXT    NOT NULL,"
        " UNIQUE (class_id, role))"
    )
    conn.execute(
        "INSERT OR IGNORE INTO class_chat (class_id, tg_chat_id, role, bound_at) "
        "SELECT id, tg_chat_id, 'parents', ? FROM klass WHERE tg_chat_id IS NOT NULL",
        (util.now_iso(),),
    )

    if "teacher_tg_user_id" not in _columns(conn, "klass"):
        conn.execute("ALTER TABLE klass ADD COLUMN teacher_tg_user_id INTEGER")
        conn.execute("ALTER TABLE klass ADD COLUMN teacher_name TEXT")

    conn.execute(
        "CREATE TABLE IF NOT EXISTS day_schedule ("
        " id                INTEGER PRIMARY KEY,"
        " class_id          INTEGER NOT NULL REFERENCES klass(id),"
        " date              TEXT    NOT NULL,"
        " lessons           TEXT    NOT NULL DEFAULT '[]',"  # JSON: ["Математика", ...]
        " bring             TEXT    NOT NULL DEFAULT '[]',"  # JSON: ["краски", ...]
        " note              TEXT,"
        " status            TEXT    NOT NULL DEFAULT 'draft'"
        "   CHECK (status IN ('draft', 'published', 'replaced', 'rejected', 'withdrawn')),"
        " source            TEXT    NOT NULL"
        "   CHECK (source IN ('teacher_chat', 'forward', 'manual')),"
        " source_text       TEXT,"
        " source_chat_id    INTEGER,"
        " source_message_id INTEGER,"
        " source_user_id    INTEGER,"  # автор исходного сообщения, если Telegram его показал
        " recognizer        TEXT,"  # 'rules' или модель, распознавшая текст
        " created_by        INTEGER REFERENCES person(id),"
        " confirmed_by      INTEGER REFERENCES person(id),"
        " created_at        TEXT    NOT NULL,"
        " published_at      TEXT,"
        " reminded_at       TEXT)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_day_published "
        "ON day_schedule(class_id, date) WHERE status = 'published'"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_day_source "
        "ON day_schedule(source_chat_id, source_message_id) "
        "WHERE source_message_id IS NOT NULL"
    )


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "person: одобрение участия председателем", _m1_person_approval),
    (2, "чаты класса с ролями, учитель, расписание на день", _m2_class_chats_and_day_schedule),
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
