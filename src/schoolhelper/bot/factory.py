"""Сборка бота и диспетчера."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .. import config
from . import access, money, onboarding
from .middleware import ContextMiddleware


def make_bot() -> Bot:
    return Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def make_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())

    context = ContextMiddleware()
    dispatcher.message.outer_middleware(context)
    dispatcher.callback_query.outer_middleware(context)
    dispatcher.my_chat_member.outer_middleware(context)  # «бота добавили в группу»

    dispatcher.include_router(access.router)
    dispatcher.include_router(onboarding.router)
    dispatcher.include_router(money.router)
    return dispatcher
