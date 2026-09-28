"""Чат с учителем — только для чтения. На уровне каждого запроса к Telegram.

Правило «бот туда не пишет» нельзя держать в хэндлерах: любой будущий код,
отвечающий через message.answer в «текущий» чат, нарушил бы его незаметно.
Поэтому проверка стоит в сессии бота — через неё проходит всё, что бот
отправляет. Разрешено только читать сведения о чате и уйти из него.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import (
    GetChat,
    GetChatAdministrators,
    GetChatMember,
    GetChatMemberCount,
    LeaveChat,
    Response,
    TelegramMethod,
)
from aiogram.methods.base import TelegramType

from ..core import logger
from ..storage import chats

log = logger.get(__name__)

_READ_ONLY_ALLOWED = (GetChat, GetChatAdministrators, GetChatMember, GetChatMemberCount, LeaveChat)


class ReadOnlyChatError(RuntimeError):
    """Попытка написать в чат с учителем или самой учительнице."""


def blocked(method: TelegramMethod) -> bool:
    chat_id = getattr(method, "chat_id", None)
    if not isinstance(chat_id, int) or isinstance(method, _READ_ONLY_ALLOWED):
        return False
    return chats.is_read_only(chat_id)


class TeacherChatGuard(BaseRequestMiddleware):
    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if blocked(method):
            log.error("blocked %s to read-only chat %s", type(method).__name__, method.chat_id)
            raise ReadOnlyChatError(f"{type(method).__name__} to read-only chat")
        return await make_request(bot, method)
