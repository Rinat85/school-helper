"""Роли: кто казначей, кто председатель. Без этого экрана систему не запустить."""

from __future__ import annotations

import sqlite3

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..core import roles as roles_mod
from ..services import people as people_svc
from ..storage import klass as klass_repo
from ..storage import persons
from .middleware import deny

router = Router(name="admin")

ASSIGNABLE = people_svc.ASSIGNABLE


@router.message(Command("роли", "roles"))
async def cmd_roles(message: Message, class_id: int, roles: set[str]) -> None:
    if not roles_mod.has(roles, "role.grant"):
        await deny(message, "role.grant")
        return
    await message.answer(**_people_view(class_id))


def _people_view(class_id: int) -> dict:
    rows = persons.active(class_id)
    keyboard = []
    for person in rows:
        own = roles_mod.roles_of(int(person["id"])) & set(ASSIGNABLE)
        badge = " · " + ", ".join(roles_mod.RU[r] for r in sorted(own)) if own else ""
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{person['display_name']}{badge}",
                    callback_data=f"role:{person['id']}",
                )
            ]
        )
    return {
        "text": (
            f"<b>Роли · {klass_repo.title(class_id)}</b>\n\n"
            "Выберите человека, чтобы выдать или снять роль."
        ),
        "reply_markup": InlineKeyboardMarkup(inline_keyboard=keyboard),
    }


@router.callback_query(F.data.startswith("role:"))
async def pick_person(callback: CallbackQuery, roles: set[str]) -> None:
    if not roles_mod.has(roles, "role.grant"):
        await deny(callback, "role.grant")
        return

    person_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(**_person_view(person_id))
    await callback.answer()


def _person_view(person_id: int) -> dict:
    person = persons.by_id(person_id)
    own = roles_mod.roles_of(person_id)
    keyboard = [
        [
            InlineKeyboardButton(
                text=("✅ " if role in own else "⬜ ") + roles_mod.RU[role],
                callback_data=f"rtog:{person_id}:{role}",
            )
        ]
        for role in ASSIGNABLE
    ]
    keyboard.append([InlineKeyboardButton(text="‹ К списку", callback_data="rback")])

    note = ""
    if roles_mod.TEACHER in own:
        note = "\n\n<i>Учителю денежный раздел не показывается.</i>"
    return {
        "text": f"<b>{persons.label(person)}</b>{note}",
        "reply_markup": InlineKeyboardMarkup(inline_keyboard=keyboard),
    }


@router.callback_query(F.data.startswith("rtog:"))
async def toggle_role(
    callback: CallbackQuery, class_id: int, person: sqlite3.Row | None, roles: set[str]
) -> None:
    if person is None or not roles_mod.has(roles, "role.grant"):
        await deny(callback, "role.grant")
        return

    _, raw_person, role = callback.data.split(":")
    target_id = int(raw_person)
    enable = role not in roles_mod.roles_of(target_id)
    try:
        people_svc.set_role(class_id, int(person["id"]), target_id, role, enable)
    except people_svc.PeopleError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.edit_text(**_person_view(target_id))
    await callback.answer()


@router.callback_query(F.data == "rback")
async def back_to_list(callback: CallbackQuery, class_id: int, roles: set[str]) -> None:
    if not roles_mod.has(roles, "role.grant"):
        await deny(callback, "role.grant")
        return
    await callback.message.edit_text(**_people_view(class_id))
    await callback.answer()


@router.message(Command("реквизиты", "card"))
async def cmd_card(message: Message, class_id: int, roles: set[str]) -> None:
    """Карта, на которую идут переводы, — показывается в каждом объявлении о сборе."""
    if not roles_mod.has(roles, "collection.create"):
        await deny(message, "collection.create")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        row = klass_repo.get(class_id)
        current = row["card_number"] if row else None
        await message.answer(
            f"Сейчас: <code>{current}</code>\n\n" if current else "Реквизиты не заданы.\n\n"
            "Задать: <code>/реквизиты 8600123412341234 Мария К.</code>"
        )
        return

    chunks = parts[1].split(maxsplit=1)
    klass_repo.set_card(class_id, chunks[0], chunks[1] if len(chunks) > 1 else "")
    await message.answer("Реквизиты обновлены.")
