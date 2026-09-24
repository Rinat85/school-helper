"""Деньги в боте: «Я оплатил» и быстрые кнопки казначея в личке.

Касса, свои взносы, создание сборов и очередь платежей — в Mini App.
"""

from __future__ import annotations

import sqlite3

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..core import logger, util
from ..core import roles as roles_mod
from ..services import collections as coll_svc
from ..services import payments as pay_svc
from ..storage import files, persons
from . import publisher
from .middleware import deny

log = logger.get(__name__)
router = Router(name="money")


class ClaimPayment(StatesGroup):
    receipt = State()


# ── Выход из диалога ────────────────────────────────────────────────────


@router.message(Command("cancel", "отмена"), StateFilter("*"), F.chat.type == "private")
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("Отменено.")


CANCEL_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="paycancel")]]
)


@router.callback_query(F.data == "paycancel")
async def pay_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отменено")


# ── Родитель заявляет оплату ────────────────────────────────────────────


def _private_state(bot: Bot, user_id: int, storage: BaseStorage) -> FSMContext:
    """Состояние диалога в личке с человеком — где бы он ни нажал кнопку.

    FSM aiogram хранится отдельно для каждого чата. «Я оплатил» под объявлением
    в группе раньше запоминал ожидание чека в группе, а чек человек присылает
    в личку — и бот его молча игнорировал.
    """
    return FSMContext(
        storage=storage, key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
    )


@router.callback_query(F.data.startswith("pay:"))
async def pay_start(
    callback: CallbackQuery, person: sqlite3.Row | None, fsm_storage: BaseStorage
) -> None:
    if person is None:
        await callback.answer("Сначала подключитесь к боту в личке.", show_alert=True)
        return

    collection_id = int(callback.data.split(":")[1])
    collection = coll_svc.get(collection_id)
    if collection is None or collection["status"] != "open":
        await callback.answer("Этот сбор уже закрыт.", show_alert=True)
        return

    contribution = coll_svc.contribution_of(collection_id, int(person["id"]))
    if contribution is None:
        await callback.answer("Вас нет в списке этого сбора.", show_alert=True)
        return

    blocked = pay_svc.claim_blocked(int(contribution["id"]))
    if blocked:
        await callback.answer(blocked[0].upper() + blocked[1:] + ".", show_alert=True)
        return

    amount = pay_svc.remaining(int(contribution["id"]))
    state = _private_state(callback.bot, callback.from_user.id, fsm_storage)
    await state.set_state(ClaimPayment.receipt)
    await state.update_data(collection_id=collection_id, amount=amount)

    partial = amount < int(contribution["expected"])
    label = "Осталось сдать" if partial else "Ваш взнос"
    text = (
        f"<b>{collection['title']}</b>\n"
        f"{label}: {util.money(amount, currency=True)}\n\n"
        "Пришлите скриншот перевода — казначей подтвердит.\n"
        "Если отдали наличными, напишите: <i>наличными</i>"
    )
    # В группе отвечаем в личку, чтобы разговор о деньгах не шёл при всех.
    try:
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=CANCEL_KEYBOARD)
        await callback.answer("Написал вам в личные сообщения.")
    except Exception:  # noqa: BLE001
        await callback.answer(
            "Не могу написать вам в личку — откройте бота и нажмите «Запустить».",
            show_alert=True,
        )
        await state.clear()


def _save_receipt(message: Message, class_id: int, person: sqlite3.Row) -> int | None:
    """Фото или файл из сообщения — в таблицу file. None, если вложения нет."""
    if message.photo:
        photo = message.photo[-1]
        return files.save_tg(
            class_id,
            photo.file_id,
            kind="receipt",
            tg_unique_id=photo.file_unique_id,
            size=photo.file_size,
            uploaded_by=int(person["id"]),
        )
    if message.document:
        return files.save_tg(
            class_id,
            message.document.file_id,
            kind="receipt",
            tg_unique_id=message.document.file_unique_id,
            mime=message.document.mime_type,
            size=message.document.file_size,
            uploaded_by=int(person["id"]),
        )
    return None


async def _submit(
    reply_to: Message,
    bot: Bot,
    person: sqlite3.Row,
    collection_id: int,
    *,
    amount: int,
    method: str,
    receipt_id: int | None,
) -> None:
    try:
        payment_id = pay_svc.claim(
            int(person["id"]),
            collection_id,
            amount=amount,
            method=method,
            receipt_file_id=receipt_id,
        )
    except pay_svc.PaymentError as exc:
        # Пока родитель искал скриншот, казначей мог уже отметить взнос сам.
        await reply_to.answer(f"Не отправил: {exc}.")
        return
    collection = coll_svc.get(collection_id)
    title = f" за «{collection['title']}»" if collection else ""
    await reply_to.answer(f"Спасибо! Передал казначею{title} — он подтвердит.")
    await publisher.notify_treasurers(bot, payment_id)


@router.message(
    StateFilter(ClaimPayment.receipt), F.chat.type == "private", F.photo | F.document | F.text
)
async def pay_receipt(
    message: Message,
    state: FSMContext,
    bot: Bot,
    class_id: int,
    person: sqlite3.Row | None,
) -> None:
    if person is None:
        await state.clear()
        return

    data = await state.get_data()
    collection_id = int(data["collection_id"])
    amount = int(data["amount"])

    receipt_id = _save_receipt(message, class_id, person)
    method = "transfer"
    if receipt_id is None:
        if message.text and "налич" in message.text.lower():
            method = "cash"
        else:
            await message.answer("Нужен скриншот перевода или слово «наличными».")
            return  # состояние сохраняем — ждём чек дальше

    await state.clear()
    await _submit(
        message, bot, person, collection_id, amount=amount, method=method, receipt_id=receipt_id
    )


@router.message(StateFilter(None), F.chat.type == "private", F.photo | F.document)
async def stray_receipt(
    message: Message, bot: Bot, class_id: int, person: sqlite3.Row | None
) -> None:
    """Чек пришёл, а бот его не ждал: выкатка стёрла состояние, или человек прислал
    скриншот сам, не нажимая «Я оплатил». Молчать нельзя — разбираемся по взносам."""
    if person is None:
        return

    waiting = pay_svc.awaiting_payment(int(person["id"]))
    if not waiting:
        await message.answer(
            "Сейчас у вас нет взносов, которые ждут оплаты, — чек некуда приложить."
        )
        return

    receipt_id = _save_receipt(message, class_id, person)
    if len(waiting) == 1:
        collection, left = waiting[0]
        await _submit(
            message,
            bot,
            person,
            int(collection["id"]),
            amount=left,
            method="transfer",
            receipt_id=receipt_id,
        )
        return

    await message.answer(
        "За какой сбор этот чек?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"{c['title']} — {util.money(left, currency=True)}",
                        callback_data=f"rcpt:{c['id']}:{receipt_id}",
                    )
                ]
                for c, left in waiting
            ]
        ),
    )


@router.callback_query(F.data.startswith("rcpt:"))
async def stray_receipt_choice(
    callback: CallbackQuery, bot: Bot, person: sqlite3.Row | None
) -> None:
    if person is None or callback.message is None:
        await callback.answer()
        return
    _, raw_collection, raw_file = callback.data.split(":")
    receipt = files.get(int(raw_file))
    if receipt is None or receipt["uploaded_by"] != person["id"]:
        await callback.answer("Чек не найден — пришлите его ещё раз.", show_alert=True)
        return

    contribution = coll_svc.contribution_of(int(raw_collection), int(person["id"]))
    left = pay_svc.remaining(int(contribution["id"])) if contribution else 0
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _submit(
        callback.message,
        bot,
        person,
        int(raw_collection),
        amount=left,
        method="transfer",
        receipt_id=int(raw_file),
    )


# ── Казначей подтверждает ───────────────────────────────────────────────


@router.callback_query(F.data.startswith("pconf:"))
async def payment_confirm(
    callback: CallbackQuery, bot: Bot, person: sqlite3.Row | None, roles: set[str]
) -> None:
    if person is None or not roles_mod.has(roles, "payment.confirm"):
        await deny(callback, "payment.confirm")
        return

    payment_id = int(callback.data.split(":")[1])
    try:
        payment = pay_svc.confirm(payment_id, int(person["id"]))
    except pay_svc.PaymentError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    payer = persons.by_id(int(payment["person_id"]))

    await _strip_buttons(callback, f"✅ Подтверждено · {persons.label(payer)}")
    await callback.answer("Записал в кассу.")
    await publisher.after_confirm(bot, payment)


@router.callback_query(F.data.startswith("prej:"))
async def payment_reject(
    callback: CallbackQuery, person: sqlite3.Row | None, roles: set[str]
) -> None:
    if person is None or not roles_mod.has(roles, "payment.confirm"):
        await deny(callback, "payment.confirm")
        return

    payment_id = int(callback.data.split(":")[1])
    try:
        payment = pay_svc.reject(payment_id, int(person["id"]), reason="не найден в выписке")
    except pay_svc.PaymentError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    payer = persons.by_id(int(payment["person_id"]))
    await _strip_buttons(callback, f"❌ Отклонено · {persons.label(payer)}")
    await callback.answer("Отметил. Родителю написал.")
    publisher.after_reject(payment)


async def _strip_buttons(callback: CallbackQuery, suffix: str) -> None:
    """Убирает кнопки, чтобы один платёж нельзя было подтвердить дважды."""
    message = callback.message
    if message is None:
        return
    try:
        if message.caption is not None:
            await message.edit_caption(caption=f"{message.caption}\n\n{suffix}", reply_markup=None)
        else:
            await message.edit_text(f"{message.html_text}\n\n{suffix}", reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
