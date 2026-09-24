"""Резолв пользователя и его ролей перед каждым апдейтом.

Правило из SPEC §9: роль берётся ТОЛЬКО из БД. Ни один хэндлер не должен
определять права по chat_id, по тому, кто написал, или по содержимому апдейта.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from ..core import roles as roles_mod
from ..storage import klass as klass_repo
from ..storage import persons


class ContextMiddleware(BaseMiddleware):
    """Кладёт в data: class_id, candidate и person (Row | None), roles (set[str])."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")

        class_id = None
        chat = getattr(event, "chat", None)
        if chat is None and isinstance(event, CallbackQuery) and event.message:
            chat = event.message.chat
        if chat is not None and chat.type in ("group", "supergroup"):
            row = klass_repo.by_chat(chat.id)
            class_id = int(row["id"]) if row else None
        if class_id is None:
            class_id = klass_repo.default_id()

        candidate = persons.by_tg(class_id, user.id) if user else None
        # Хэндлеры видят «своего» только в одобренном участнике: ждущий одобрения
        # или исключённый для них посторонний. Сырая запись — для онбординга.
        person = candidate if persons.is_member(candidate) else None

        data["class_id"] = class_id
        data["candidate"] = candidate
        data["person"] = person
        data["roles"] = roles_mod.roles_of(int(person["id"])) if person else set()
        return await handler(event, data)


def display_name_of(user: User) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or (user.username or f"id{user.id}")


async def deny(event: Message | CallbackQuery, permission: str) -> None:
    """Единый отказ в правах — с понятной формулировкой, а не 'нет доступа'."""
    from . import texts

    allowed = roles_mod.PERMISSIONS.get(permission, frozenset())
    names = ", ".join(roles_mod.RU[r] for r in sorted(allowed))
    text = texts.NO_PERMISSION.format(roles=names)
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)
