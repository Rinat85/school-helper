"""Люди класса и их роли.

Правила выдачи ролей живут здесь, а не в интерфейсе: ими пользуются и бот,
и Mini App, и они не должны разойтись.
"""

from __future__ import annotations

import sqlite3

from ..core import roles as roles_mod
from ..core import util
from ..storage import db, journal, persons

# Роли, которые можно выдать из интерфейса. parent есть у всех и не снимается,
# admin выдаётся только через BOOTSTRAP_CHAIR_TG_ID в .env.
ASSIGNABLE = (roles_mod.TREASURER, roles_mod.CHAIR, roles_mod.AUDITOR, roles_mod.TEACHER)


class PeopleError(Exception):
    """Нарушение правил — текст показывается пользователю как есть."""


def listing(class_id: int, *, include_left: bool = False) -> list[dict]:
    where = "class_id = ?" if include_left else "class_id = ? AND status = 'active'"
    rows = db.query(
        f"SELECT * FROM person WHERE {where} "
        "ORDER BY status, approved_at IS NOT NULL, display_name",  # ждущие — сверху
        class_id,
    )
    return [view(row) for row in rows]


def view(row: sqlite3.Row) -> dict:
    own = roles_mod.roles_of(int(row["id"]))
    return {
        "id": int(row["id"]),
        "name": row["display_name"],
        "child": row["child_name"],
        "username": row["tg_username"],
        "in_telegram": row["tg_user_id"] is not None,
        "bot_connected": bool(row["dm_open"]),
        "status": row["status"],
        "approved": row["approved_at"] is not None,
        "roles": sorted(own & set(ASSIGNABLE)),
        "is_admin": roles_mod.ADMIN in own,
    }


def get(class_id: int, person_id: int) -> sqlite3.Row:
    """Человек этого класса — чужой class_id не должен проходить никогда."""
    row = persons.by_id(person_id)
    if row is None or int(row["class_id"]) != class_id:
        raise PeopleError("человек не найден")
    return row


def add_offline(class_id: int, actor_id: int, name: str, child: str | None) -> int:
    """Родитель без Telegram — его взносы казначей отмечает вручную (SPEC §5.5)."""
    name = name.strip()
    if not name:
        raise PeopleError("нужно имя")
    person_id = persons.create(class_id, name[:64], child_name=(child or "").strip()[:64] or None)
    journal.audit(
        class_id, actor_id, "person.add", object_type="person", object_id=person_id,
        after={"name": name, "offline": True},
    )
    return person_id


def update(class_id: int, actor_id: int, person_id: int, name: str, child: str | None) -> None:
    row = get(class_id, person_id)
    name = name.strip()
    if not name:
        raise PeopleError("нужно имя")
    child = (child or "").strip() or None
    persons.set_names(person_id, name[:64], child[:64] if child else None)
    journal.audit(
        class_id, actor_id, "person.update", object_type="person", object_id=person_id,
        before={"name": row["display_name"], "child": row["child_name"]},
        after={"name": name, "child": child},
    )


def approve(class_id: int, actor_id: int, person_id: int) -> list[sqlite3.Row]:
    """Впустить в класс. Возвращает открытые сборы, в которые человек записан."""
    from . import collections as coll_svc  # здесь: collections тоже импортирует persons

    row = get(class_id, person_id)
    if not persons.is_pending(row):
        raise PeopleError("заявка уже рассмотрена")
    persons.approve(person_id, actor_id)
    enrolled = coll_svc.enroll_in_open(class_id, person_id)
    journal.audit(
        class_id, actor_id, "person.approve", object_type="person", object_id=person_id,
        after={"enrolled_collections": [int(c["id"]) for c in enrolled]},
    )
    return enrolled


def decline(class_id: int, actor_id: int, person_id: int) -> None:
    """Не впускать. Человек сможет подать заявку снова по ссылке."""
    row = get(class_id, person_id)
    if not persons.is_pending(row):
        raise PeopleError("заявка уже рассмотрена")
    persons.mark_left(person_id)
    journal.audit(class_id, actor_id, "person.decline", object_type="person", object_id=person_id)


def mark_left(class_id: int, actor_id: int, person_id: int) -> None:
    """Ушёл из класса. Запись и история денег остаются, роли снимаются."""
    get(class_id, person_id)
    if person_id == actor_id:
        raise PeopleError("нельзя исключить самого себя")
    for role in ASSIGNABLE:
        _revoke(class_id, actor_id, person_id, role)
    persons.mark_left(person_id)
    journal.audit(class_id, actor_id, "person.leave", object_type="person", object_id=person_id)


def set_role(class_id: int, actor_id: int, person_id: int, role: str, enabled: bool) -> None:
    if role not in ASSIGNABLE:
        raise PeopleError("эту роль нельзя выдать из интерфейса")
    row = get(class_id, person_id)
    if row["status"] != "active":
        raise PeopleError("человек уже не в классе")
    if row["approved_at"] is None:
        raise PeopleError("сначала одобрите участие")

    if enabled:
        if role in roles_mod.roles_of(person_id):
            return
        roles_mod.grant(person_id, role, by=actor_id, now=util.now_iso())
        journal.audit(
            class_id, actor_id, "role.grant", object_type="person", object_id=person_id,
            after={"role": role},
        )
        return

    _revoke(class_id, actor_id, person_id, role)


def _revoke(class_id: int, actor_id: int, person_id: int, role: str) -> None:
    if role not in roles_mod.roles_of(person_id):
        return
    if role == roles_mod.CHAIR:
        if person_id == actor_id:
            raise PeopleError("нельзя снять председателя с самого себя — сначала назначьте другого")
        if len(persons.with_role(class_id, roles_mod.CHAIR)) <= 1:
            raise PeopleError("в классе должен остаться хотя бы один председатель")
    roles_mod.revoke(person_id, role, by=actor_id, now=util.now_iso())
    journal.audit(
        class_id, actor_id, "role.revoke", object_type="person", object_id=person_id,
        before={"role": role},
    )
