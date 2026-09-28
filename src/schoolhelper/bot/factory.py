"""Сборка бота и диспетчера."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .. import config
from . import access, money, onboarding, schedule
from .guard import TeacherChatGuard
from .middleware import ContextMiddleware


def make_bot() -> Bot:
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    bot.session.middleware(TeacherChatGuard())  # в чат с учителем — ни слова
    return bot


def make_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())

    context = ContextMiddleware()
    dispatcher.message.outer_middleware(context)
    dispatcher.callback_query.outer_middleware(context)
    dispatcher.my_chat_member.outer_middleware(context)  # «бота добавили в группу»

    dispatcher.include_router(access.router)
    dispatcher.include_router(onboarding.router)
    # Раньше денег: пересланное председателем расписание с подписью к фото
    # не должно приниматься за чек.
    dispatcher.include_router(schedule.router)
    dispatcher.include_router(money.router)
    return dispatcher
