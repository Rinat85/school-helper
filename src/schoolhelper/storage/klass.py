"""Класс: создание при первом запуске, реквизиты, учительница."""

from __future__ import annotations

import sqlite3

from .. import config
from ..core import util
from . import db


def ensure_seeded() -> int:
    """Создаёт класс из .env, если БД пустая. Возвращает class_id."""
    row = db.one("SELECT id FROM klass ORDER BY id LIMIT 1")
    if row:
        return int(row["id"])
    return db.insert(
        "klass",
        name=config.CLASS_NAME,
        school=config.CLASS_SCHOOL,
        timezone=config.CLASS_TZ,
        currency=config.CLASS_CURRENCY,
        created_at=util.now_iso(),
    )


def get(class_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM klass WHERE id = ?", class_id)


def default_id() -> int:
    """MVP работает с одним классом; class_id всё равно проносится везде."""
    return ensure_seeded()


# Чаты класса — в storage/chats.py. Колонка klass.tg_chat_id осталась от первой
# версии и больше не читается (миграция 2 перенесла её в class_chat).


def set_teacher(class_id: int, tg_user_id: int | None, name: str | None) -> None:
    """Чьи сообщения в чате с учителем бот читает как расписание."""
    db.execute(
        "UPDATE klass SET teacher_tg_user_id = ?, teacher_name = ? WHERE id = ?",
        tg_user_id,
        name,
        class_id,
    )


def is_teacher(class_id: int, tg_user_id: int) -> bool:
    row = get(class_id)
    return bool(row) and row["teacher_tg_user_id"] == tg_user_id


def set_card(class_id: int, number: str, holder: str) -> None:
    db.execute(
        "UPDATE klass SET card_number = ?, card_holder = ? WHERE id = ?",
        number,
        holder,
        class_id,
    )


def set_info(class_id: int, name: str, school: str | None) -> None:
    db.execute(
        "UPDATE klass SET name = ?, school = ? WHERE id = ?", name, school, class_id
    )


def title(class_id: int) -> str:
    row = get(class_id)
    if not row:
        return ""
    return f"{row['name']} · {row['school']}" if row["school"] else row["name"]
