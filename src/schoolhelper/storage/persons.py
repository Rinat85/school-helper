"""Родители, дети, подключение к боту.

Участник класса — активный И одобренный председателем. Пришедший по ссылке
сначала ждёт одобрения: ссылку пересылают, и без этого в класс мог войти
кто угодно. Пока ждёт — не видит кассу, не учитывается в сборах, не получает
рассылок. Вся выборка «кто в классе» идёт через MEMBER.
"""

from __future__ import annotations

import sqlite3

from .. import config
from ..core import roles as roles_mod
from ..core import util
from . import db


def by_tg(class_id: int, tg_user_id: int) -> sqlite3.Row | None:
    return db.one(
        "SELECT * FROM person WHERE class_id = ? AND tg_user_id = ?", class_id, tg_user_id
    )


def by_id(person_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM person WHERE id = ?", person_id)


MEMBER = "status = 'active' AND approved_at IS NOT NULL"


def is_member(row: sqlite3.Row | None) -> bool:
    return row is not None and row["status"] == "active" and row["approved_at"] is not None


def is_pending(row: sqlite3.Row | None) -> bool:
    return row is not None and row["status"] == "active" and row["approved_at"] is None


def active(class_id: int) -> list[sqlite3.Row]:
    """Участники класса: с них собирают взносы, им пишет бот."""
    return db.query(
        f"SELECT * FROM person WHERE class_id = ? AND {MEMBER} ORDER BY display_name",
        class_id,
    )


def count_active(class_id: int) -> int:
    return int(
        db.scalar(f"SELECT COUNT(*) FROM person WHERE class_id = ? AND {MEMBER}", class_id) or 0
    )


def pending(class_id: int) -> list[sqlite3.Row]:
    """Пришли по ссылке и ждут одобрения председателя."""
    return db.query(
        "SELECT * FROM person WHERE class_id = ? AND status = 'active' AND approved_at IS NULL "
        "ORDER BY joined_at",
        class_id,
    )


def not_connected(class_id: int) -> list[sqlite3.Row]:
    """Не нажали /start — бот физически не может написать им первым (SPEC §5.5)."""
    return db.query(
        f"SELECT * FROM person WHERE class_id = ? AND {MEMBER} AND dm_open = 0 "
        "ORDER BY display_name",
        class_id,
    )


def with_role(class_id: int, role: str) -> list[sqlite3.Row]:
    return db.query(
        "SELECT p.* FROM person p "
        "JOIN person_role r ON r.person_id = p.id AND r.revoked_at IS NULL "
        "WHERE p.class_id = ? AND p.status = 'active' AND p.approved_at IS NOT NULL "
        "AND r.role = ? "
        "ORDER BY p.display_name",
        class_id,
        role,
    )


def create(
    class_id: int,
    display_name: str,
    *,
    tg_user_id: int | None = None,
    tg_username: str | None = None,
    child_name: str | None = None,
    dm_open: bool = False,
    approved_by: int | None = None,
    approved: bool = True,
) -> int:
    """approved=False — пришёл сам по ссылке и ждёт председателя."""
    now = util.now_iso()
    person_id = db.insert(
        "person",
        class_id=class_id,
        tg_user_id=tg_user_id,
        tg_username=tg_username,
        display_name=display_name,
        child_name=child_name,
        dm_open=int(dm_open),
        joined_at=now,
        approved_at=now if approved else None,
        approved_by=approved_by,
    )
    roles_mod.grant(person_id, roles_mod.PARENT, by=None, now=util.now_iso())
    return person_id


def upsert_from_tg(
    class_id: int,
    tg_user_id: int,
    *,
    display_name: str,
    tg_username: str | None = None,
) -> sqlite3.Row:
    """Находит или заводит человека по его Telegram-аккаунту и помечает личку открытой."""
    row = by_tg(class_id, tg_user_id)
    bootstrap = bool(config.BOOTSTRAP_CHAIR_TG_ID) and tg_user_id == config.BOOTSTRAP_CHAIR_TG_ID
    if row is None:
        # Сам пришёл по ссылке — ждёт председателя. Председатель из .env — сразу свой.
        person_id = create(
            class_id,
            display_name,
            tg_user_id=tg_user_id,
            tg_username=tg_username,
            dm_open=True,
            approved=bootstrap,
        )
        _bootstrap_chair(person_id, tg_user_id)
        row = by_id(person_id)
        assert row is not None
    elif row["status"] == "left":
        # Вернулся после исключения или отказа — снова через одобрение.
        db.execute(
            "UPDATE person SET status = 'active', left_at = NULL, approved_at = NULL, "
            "approved_by = NULL, dm_open = 1, tg_username = COALESCE(?, tg_username) "
            "WHERE id = ?",
            tg_username,
            row["id"],
        )
        row = by_id(row["id"])
        assert row is not None
    else:
        db.execute(
            "UPDATE person SET dm_open = 1, tg_username = COALESCE(?, tg_username) WHERE id = ?",
            tg_username,
            row["id"],
        )
        row = by_id(row["id"])
        assert row is not None
    return row


def _bootstrap_chair(person_id: int, tg_user_id: int) -> None:
    """Первому запуску нужен хоть один председатель — иначе роли некому выдать."""
    if config.BOOTSTRAP_CHAIR_TG_ID and tg_user_id == config.BOOTSTRAP_CHAIR_TG_ID:
        now = util.now_iso()
        roles_mod.grant(person_id, roles_mod.CHAIR, by=None, now=now)
        roles_mod.grant(person_id, roles_mod.ADMIN, by=None, now=now)


def set_names(person_id: int, display_name: str, child_name: str | None) -> None:
    db.execute(
        "UPDATE person SET display_name = ?, child_name = ? WHERE id = ?",
        display_name,
        child_name,
        person_id,
    )


def approve(person_id: int, by: int) -> None:
    db.execute(
        "UPDATE person SET approved_at = ?, approved_by = ? WHERE id = ? AND approved_at IS NULL",
        util.now_iso(),
        by,
        person_id,
    )


def mark_left(person_id: int) -> None:
    """Ушёл из класса. Запись остаётся — она нужна истории денег."""
    db.execute(
        "UPDATE person SET status = 'left', left_at = ? WHERE id = ?", util.now_iso(), person_id
    )


def mark_dm_closed(person_id: int) -> None:
    """Заблокировал бота — персональные уведомления больше не доходят."""
    db.execute("UPDATE person SET dm_open = 0 WHERE id = ?", person_id)


def label(row: sqlite3.Row | None) -> str:
    if row is None:
        return "—"
    if row["child_name"]:
        return f"{row['display_name']} ({row['child_name']})"
    return str(row["display_name"])
