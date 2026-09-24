"""Что бот показывает в меню и какой кнопкой открывается Mini App.

Всё, кроме /start, живёт в приложении. Команд, которые видит родитель, ровно
одна; /setup видят только администраторы групп, и то лишь пока бот в группе.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from .. import config
from ..core import logger

log = logger.get(__name__)


def app_url() -> str | None:
    """Mini App открывается только по https — без домена кнопки нет."""
    return config.PUBLIC_URL if config.PUBLIC_URL.startswith("https://") else None


def app_keyboard(text: str = "Открыть приложение класса") -> InlineKeyboardMarkup | None:
    """Кнопка, открывающая Mini App. Работает только в личке — в группах Telegram
    такие кнопки запрещает."""
    url = app_url()
    if url is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))]]
    )


async def setup_commands(bot: Bot) -> None:
    """Список команд в меню «/». Старые (/касса, /сбор, /роли…) стираются."""
    try:
        await bot.delete_my_commands(scope=BotCommandScopeDefault())
        await bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(
            [BotCommand(command="start", description="Помощник класса")],
            scope=BotCommandScopeAllPrivateChats(),
        )
        await bot.set_my_commands(
            [BotCommand(command="setup", description="Привязать эту группу к классу")],
            scope=BotCommandScopeAllChatAdministrators(),
        )
    except Exception:  # noqa: BLE001 - без меню бот работает
        log.exception("cannot set bot commands")
