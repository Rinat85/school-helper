"""Класс: создание при первом запуске, привязка к группе Telegram."""

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


def by_chat(tg_chat_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM klass WHERE tg_chat_id = ?", tg_chat_id)


def bind_chat(class_id: int, tg_chat_id: int) -> None:
    db.execute("UPDATE klass SET tg_chat_id = ? WHERE id = ?", tg_chat_id, class_id)


def set_card(class_id: int, number: str, holder: str) -> None:
    db.execute(
        "UPDATE klass SET card_number = ?, card_holder = ? WHERE id = ?",
        number,
        holder,
        class_id,
    )


def title(class_id: int) -> str:
    row = get(class_id)
    if not row:
        return ""
    return f"{row['name']} · {row['school']}" if row["school"] else row["name"]
