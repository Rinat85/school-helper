"""Путь чека: «Я оплатил» → скриншот → заявка казначею.

Найдено на живом тесте: нажали «Я оплатил» под объявлением в группе, прислали
скриншот в личку — и тишина, ни у родителя, ни у казначея. Ожидание чека
запоминалось в группе, а не в личке.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from schoolhelper.bot import money as money_bot
from schoolhelper.core import roles as roles_mod
from schoolhelper.core import util
from schoolhelper.services import collections as coll_svc
from schoolhelper.storage import db, persons

BOT_ID = 123
PARENT_TG = 3


@pytest.fixture()
def cls(fresh_db):
    class_id = fresh_db
    treasurer = persons.create(class_id, "Мария", tg_user_id=2, dm_open=True)
    roles_mod.grant(treasurer, roles_mod.TREASURER, by=None, now=util.now_iso())
    parent = persons.create(class_id, "Анна", tg_user_id=PARENT_TG, dm_open=True)
    return {"class_id": class_id, "treasurer": treasurer, "parent": parent}


def _collection(cls, title="Подарки на Новый год", amount=30_000) -> int:
    collection_id = coll_svc.create(
        cls["class_id"], cls["treasurer"], title=title, amount_per_person=amount
    )
    coll_svc.open_(collection_id, cls["treasurer"])
    return collection_id


def _bot():
    return SimpleNamespace(
        id=BOT_ID, send_message=AsyncMock(), send_photo=AsyncMock()
    )


def _photo_message():
    return SimpleNamespace(
        photo=[SimpleNamespace(file_id="tg-photo", file_unique_id="u1", file_size=1000)],
        document=None,
        text=None,
        answer=AsyncMock(),
    )


def _claims(cls):
    return db.query(
        "SELECT * FROM payment WHERE person_id = ? AND status = 'claimed'", cls["parent"]
    )


def _private(storage) -> FSMContext:
    return FSMContext(
        storage=storage, key=StorageKey(bot_id=BOT_ID, chat_id=PARENT_TG, user_id=PARENT_TG)
    )


async def test_button_in_group_waits_for_receipt_in_private_chat(cls):
    collection_id = _collection(cls)
    storage = MemoryStorage()
    bot = _bot()
    callback = SimpleNamespace(
        data=f"pay:{collection_id}",
        bot=bot,
        from_user=SimpleNamespace(id=PARENT_TG),
        message=SimpleNamespace(chat=SimpleNamespace(id=-100, type="supergroup")),
        answer=AsyncMock(),
    )

    await money_bot.pay_start(callback, persons.by_id(cls["parent"]), storage)

    # Ждём чек именно в личке — туда человек его и пришлёт.
    private = _private(storage)
    assert await private.get_state() == money_bot.ClaimPayment.receipt.state

    # И скриншот в личке доходит до казначея.
    message = _photo_message()
    parent = persons.by_id(cls["parent"])
    await money_bot.pay_receipt(message, private, bot, cls["class_id"], parent)

    assert len(_claims(cls)) == 1
    assert _claims(cls)[0]["receipt_id"] is not None
    assert "Спасибо" in message.answer.await_args.args[0]
    bot.send_photo.assert_awaited()  # карточка с чеком ушла казначею
    assert await private.get_state() is None


async def test_unexpected_receipt_goes_to_the_only_open_collection(cls):
    """Состояние стёрла выкатка — чек всё равно не должен пропасть молча."""
    _collection(cls)
    message = _photo_message()

    await money_bot.stray_receipt(message, _bot(), cls["class_id"], persons.by_id(cls["parent"]))

    claims = _claims(cls)
    assert len(claims) == 1 and claims[0]["amount"] == 30_000
    assert "Подарки на Новый год" in message.answer.await_args.args[0]


async def test_unexpected_receipt_asks_which_collection(cls):
    first = _collection(cls, title="Цветы", amount=15_000)
    _collection(cls, title="Экскурсия", amount=50_000)
    message = _photo_message()
    bot = _bot()

    await money_bot.stray_receipt(message, bot, cls["class_id"], persons.by_id(cls["parent"]))

    assert _claims(cls) == []
    buttons = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
    assert len(buttons) == 2

    choice = next(b[0] for b in buttons if b[0].callback_data.startswith(f"rcpt:{first}:"))
    callback = SimpleNamespace(
        data=choice.callback_data,
        message=SimpleNamespace(edit_reply_markup=AsyncMock(), answer=AsyncMock()),
        answer=AsyncMock(),
    )
    await money_bot.stray_receipt_choice(callback, bot, persons.by_id(cls["parent"]))

    claims = _claims(cls)
    assert len(claims) == 1 and claims[0]["amount"] == 15_000


async def test_unexpected_receipt_with_nothing_to_pay(cls):
    message = _photo_message()
    await money_bot.stray_receipt(message, _bot(), cls["class_id"], persons.by_id(cls["parent"]))
    assert "нет взносов" in message.answer.await_args.args[0]
    assert _claims(cls) == []


async def test_someone_elses_receipt_cannot_be_attached(cls):
    """В callback_data лежит id файла — чужой чек подсунуть нельзя."""
    first = _collection(cls, title="Цветы", amount=15_000)
    _collection(cls, title="Экскурсия", amount=50_000)
    message = _photo_message()
    await money_bot.stray_receipt(
        message, _bot(), cls["class_id"], persons.by_id(cls["treasurer"])
    )
    foreign = message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    file_id = foreign.split(":")[2]

    callback = SimpleNamespace(
        data=f"rcpt:{first}:{file_id}",
        message=SimpleNamespace(edit_reply_markup=AsyncMock(), answer=AsyncMock()),
        answer=AsyncMock(),
    )
    await money_bot.stray_receipt_choice(callback, _bot(), persons.by_id(cls["parent"]))

    assert _claims(cls) == []
    assert "не найден" in callback.answer.await_args.args[0]
