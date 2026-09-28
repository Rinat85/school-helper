"""Три чата класса: роли, привязка через /setup и «в чат с учителем — ни слова»."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import GetChat, LeaveChat, SendMessage, SetMessageReaction

from schoolhelper import config
from schoolhelper.bot import guard, onboarding
from schoolhelper.core import roles as roles_mod
from schoolhelper.storage import chats
from schoolhelper.storage import klass as klass_repo

CHAIR_TG = 1000
CHAIR = {roles_mod.PARENT, roles_mod.CHAIR}
PARENTS_CHAT, TEACHER_CHAT = -100, -300


@pytest.fixture()
def bound(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_CHAIR_TG_ID", CHAIR_TG)
    chats.bind(fresh_db, PARENTS_CHAT, chats.PARENTS, "Родители 1 В", CHAIR_TG)
    chats.bind(fresh_db, TEACHER_CHAT, chats.TEACHER, "1 В и учитель", CHAIR_TG)
    return fresh_db


# ── Хранилище ───────────────────────────────────────────────────────────


def test_role_has_one_chat_and_chat_has_one_role(bound):
    previous = chats.bind(bound, -500, chats.PARENTS, "Новая группа", CHAIR_TG)
    assert previous == PARENTS_CHAT
    assert chats.chat_id(bound, chats.PARENTS) == -500
    assert chats.get(PARENTS_CHAT) is None

    chats.bind(bound, -500, chats.COMMITTEE, "Новая группа", CHAIR_TG)
    assert chats.chat_id(bound, chats.PARENTS) is None
    assert chats.get(-500)["role"] == chats.COMMITTEE


# ── Чат с учителем только для чтения ────────────────────────────────────


def test_guard_blocks_any_write_to_teacher_chat(bound):
    assert guard.blocked(SendMessage(chat_id=TEACHER_CHAT, text="Привет"))
    assert guard.blocked(
        SetMessageReaction(chat_id=TEACHER_CHAT, message_id=1, reaction=[])
    )
    assert not guard.blocked(SendMessage(chat_id=PARENTS_CHAT, text="Сбор"))


def test_guard_allows_reading_and_leaving(bound):
    assert not guard.blocked(GetChat(chat_id=TEACHER_CHAT))
    assert not guard.blocked(LeaveChat(chat_id=TEACHER_CHAT))


def test_guard_blocks_dm_to_teacher(bound):
    """Учительница — не член родительского класса: в личку ей бот тоже не пишет."""
    klass_repo.set_teacher(bound, 777, "Гульнара Рашидовна")
    assert guard.blocked(SendMessage(chat_id=777, text="Напоминание"))


async def test_guard_middleware_stops_request(bound):
    make_request = AsyncMock()
    with pytest.raises(guard.ReadOnlyChatError):
        await guard.TeacherChatGuard()(
            make_request, None, SendMessage(chat_id=TEACHER_CHAT, text="x")
        )
    make_request.assert_not_awaited()


# ── /setup ──────────────────────────────────────────────────────────────


def _setup_message(chat_id: int, user_id: int = CHAIR_TG):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Какая-то группа"),
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
    )


async def test_setup_asks_role_in_private_not_in_group(fresh_db, monkeypatch):
    """Вопрос «что это за чат» — в личку: вдруг это чат с учителем."""
    monkeypatch.setattr(config, "BOOTSTRAP_CHAIR_TG_ID", CHAIR_TG)
    bot = SimpleNamespace(send_message=AsyncMock())
    message = _setup_message(-700)

    await onboarding.cmd_setup(message, bot, fresh_db, set())

    message.answer.assert_not_awaited()
    chat_id, _text = bot.send_message.await_args.args
    assert chat_id == CHAIR_TG
    buttons = bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in buttons] == [
        "bind:parents:-700",
        "bind:committee:-700",
        "bind:teacher:-700",
    ]


async def test_stranger_setup_in_teacher_chat_gets_no_reply(bound):
    message = _setup_message(TEACHER_CHAT, user_id=5)
    await onboarding.cmd_setup(message, SimpleNamespace(send_message=AsyncMock()), bound, set())
    message.answer.assert_not_awaited()


def _bind_callback(role: str, chat_id: int):
    return SimpleNamespace(
        data=f"bind:{role}:{chat_id}",
        from_user=SimpleNamespace(id=CHAIR_TG),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


def _bot_in_chat(title: str):
    return SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(title=title)),
        send_message=AsyncMock(),
    )


async def test_binding_teacher_chat_writes_nothing_there(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_CHAIR_TG_ID", CHAIR_TG)
    bot = _bot_in_chat("1 В и учитель")

    await onboarding.bind_chat_role(_bind_callback("teacher", -300), bot, fresh_db, set())

    assert chats.get(-300)["role"] == chats.TEACHER
    bot.send_message.assert_not_awaited()


async def test_binding_parents_chat_posts_join_button(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_CHAIR_TG_ID", CHAIR_TG)
    bot = _bot_in_chat("Родители 1 В")

    await onboarding.bind_chat_role(_bind_callback("parents", -100), bot, fresh_db, set())

    assert chats.chat_id(fresh_db, chats.PARENTS) == -100
    assert bot.send_message.await_args.args[0] == -100
    button = bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Подключиться"


async def test_parent_cannot_bind_via_button(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_CHAIR_TG_ID", CHAIR_TG)
    callback = _bind_callback("parents", -100)
    callback.from_user = SimpleNamespace(id=5)

    await onboarding.bind_chat_role(callback, _bot_in_chat("x"), fresh_db, {roles_mod.PARENT})

    assert chats.of_class(fresh_db) == []


async def test_chair_role_binds_too(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_CHAIR_TG_ID", 0)
    await onboarding.bind_chat_role(
        _bind_callback("committee", -200), _bot_in_chat("Комитет"), fresh_db, CHAIR
    )
    assert chats.chat_id(fresh_db, chats.COMMITTEE) == -200
