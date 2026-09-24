"""Бот публичный: кто может привязать группу и где бот соглашается работать."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from schoolhelper import config
from schoolhelper.bot import access, menu
from schoolhelper.core import roles as roles_mod
from schoolhelper.storage import klass as klass_repo

CHAIR = {roles_mod.PARENT, roles_mod.CHAIR}
PARENT = {roles_mod.PARENT}


@pytest.fixture()
def bootstrap(monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_CHAIR_TG_ID", 1000)


# ── Привязка группы ─────────────────────────────────────────────────────


def test_chair_binds_group(bootstrap):
    assert access.may_bind_group(5, CHAIR)


def test_parent_cannot_bind_group(bootstrap):
    assert not access.may_bind_group(5, PARENT)


def test_stranger_cannot_bind_group_even_without_chair(bootstrap):
    """Раньше, пока председателя нет, /setup срабатывал от кого угодно."""
    assert not access.may_bind_group(5, set())


def test_future_chair_binds_before_first_start(bootstrap):
    """Будущий председатель из .env ещё не в базе, но группу привязать должен."""
    assert access.may_bind_group(1000, set())


def test_no_bootstrap_means_no_exception(monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_CHAIR_TG_ID", 0)
    assert not access.may_bind_group(0, set())


# ── Бота добавили в группу ──────────────────────────────────────────────


def test_stays_in_class_group_whoever_added(bootstrap):
    assert access.may_stay(-100, bound_chat_id=-100, adder_tg_id=5, adder_roles=PARENT)


def test_leaves_foreign_group_added_by_parent(bootstrap):
    """Родитель позвал бота в посторонний чат — там не должно быть ничего о классе."""
    assert not access.may_stay(-200, bound_chat_id=-100, adder_tg_id=5, adder_roles=PARENT)


def test_chair_may_bring_bot_to_new_group(bootstrap):
    """Переезд класса в новый чат: председатель добавляет, потом делает /setup."""
    assert access.may_stay(-200, bound_chat_id=-100, adder_tg_id=5, adder_roles=CHAIR)


def _event(chat_id: int, adder: int, status: str = "member"):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=adder),
        new_chat_member=SimpleNamespace(status=status),
    )


async def test_handler_leaves_foreign_group(fresh_db, bootstrap):
    klass_repo.bind_chat(fresh_db, -100)
    bot = SimpleNamespace(send_message=AsyncMock(), leave_chat=AsyncMock())

    await access.added_to_group(_event(-200, adder=5), bot, fresh_db, PARENT)

    bot.leave_chat.assert_awaited_once_with(-200)


async def test_handler_stays_in_class_group(fresh_db, bootstrap):
    klass_repo.bind_chat(fresh_db, -100)
    bot = SimpleNamespace(send_message=AsyncMock(), leave_chat=AsyncMock())

    await access.added_to_group(_event(-100, adder=5), bot, fresh_db, PARENT)

    bot.leave_chat.assert_not_awaited()


async def test_handler_ignores_removal(fresh_db, bootstrap):
    bot = SimpleNamespace(send_message=AsyncMock(), leave_chat=AsyncMock())
    await access.added_to_group(_event(-200, adder=5, status="left"), bot, fresh_db, PARENT)
    bot.leave_chat.assert_not_awaited()


# ── Кнопка приложения ───────────────────────────────────────────────────


def test_no_app_button_without_https(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    assert menu.app_keyboard() is None


def test_app_button_opens_mini_app(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "https://class.example.com")
    button = menu.app_keyboard().inline_keyboard[0][0]
    assert button.web_app.url == "https://class.example.com"
