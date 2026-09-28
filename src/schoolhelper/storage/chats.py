"""Чаты класса и их роли.

У класса три чата, и бот ведёт себя в них по-разному:
  * parents   — родительский: объявления о сборах, «Я оплатил», приглашение;
  * committee — родительский комитет: служебный (сейчас — полигон для проверки);
  * teacher   — чат с учителем: бот только читает расписание. Писать туда нельзя
                ничего и никогда — это держит bot/guard.py на уровне всех запросов.
"""

from __future__ import annotations

import sqlite3

from ..core import util
from . import db

PARENTS = "parents"
COMMITTEE = "committee"
TEACHER = "teacher"
ROLES = (PARENTS, COMMITTEE, TEACHER)
RU = {PARENTS: "Родительский", COMMITTEE: "Комитет", TEACHER: "С учителем"}


def get(tg_chat_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM class_chat WHERE tg_chat_id = ?", tg_chat_id)


def of_class(class_id: int) -> list[sqlite3.Row]:
    return db.query("SELECT * FROM class_chat WHERE class_id = ? ORDER BY role", class_id)


def chat_id(class_id: int, role: str) -> int | None:
    """Чат класса с этой ролью — например, куда объявлять сборы."""
    value = db.scalar(
        "SELECT tg_chat_id FROM class_chat WHERE class_id = ? AND role = ?", class_id, role
    )
    return int(value) if value is not None else None


def bind(
    class_id: int, tg_chat_id: int, role: str, title: str | None, by_tg_user_id: int
) -> int | None:
    """Назначает чату роль. У роли — один чат, у чата — одна роль.

    Возвращает чат, который до этого занимал роль (его привязка снята), если был.
    """
    if role not in ROLES:
        raise ValueError(f"unknown chat role: {role}")
    with db.tx():
        previous = chat_id(class_id, role)
        db.execute(
            "DELETE FROM class_chat WHERE tg_chat_id = ? OR (class_id = ? AND role = ?)",
            tg_chat_id,
            class_id,
            role,
        )
        db.insert(
            "class_chat",
            class_id=class_id,
            tg_chat_id=tg_chat_id,
            role=role,
            title=title,
            bound_by=by_tg_user_id,
            bound_at=util.now_iso(),
        )
    return previous if previous != tg_chat_id else None


def unbind(class_id: int, role: str) -> bool:
    cur = db.execute("DELETE FROM class_chat WHERE class_id = ? AND role = ?", class_id, role)
    return cur.rowcount > 0


def is_read_only(tg_chat_id: int) -> bool:
    """Сюда бот не пишет: чат с учителем и личка самой учительницы."""
    return bool(
        db.scalar(
            "SELECT 1 FROM class_chat WHERE tg_chat_id = ? AND role = 'teacher' "
            "UNION ALL SELECT 1 FROM klass WHERE teacher_tg_user_id = ? LIMIT 1",
            tg_chat_id,
            tg_chat_id,
        )
    )
