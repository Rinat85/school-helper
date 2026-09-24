"""Деньги в боте: «Я оплатил», быстрые кнопки казначея, /касса и /мои.

Создание сборов и очередь платежей — в Mini App (кнопка «Класс»).
"""

from __future__ import annotations

import sqlite3

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    Message,
)

from ..core import logger, util
from ..core import roles as roles_mod
from ..services import collections as coll_svc
from ..services import payments as pay_svc
from ..storage import files, money, persons
from . import publisher, texts
from .middleware import deny

log = logger.get(__name__)
router = Router(name="money")


class ClaimPayment(StatesGroup):
    receipt = State()


# ── Выход из диалога ────────────────────────────────────────────────────


@router.message(Command("cancel", "отмена"), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("Отменено.")


# ── Родитель заявляет оплату ────────────────────────────────────────────


@router.callback_query(F.data.startswith("pay:"))
async def pay_start(
    callback: CallbackQuery, state: FSMContext, person: sqlite3.Row | None
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
    await state.set_state(ClaimPayment.receipt)
    await state.update_data(collection_id=collection_id, amount=amount)

    partial = amount < int(contribution["expected"])
    text = (
        f"<b>{collection['title']}</b>\n"
        f"{'Осталось сдать' if partial else 'Ваш взнос'}: {util.money(amount, currency=True)}\n\n"
        "Пришлите скриншот перевода — казначей подтвердит.\n"
        "Если отдали наличными, напишите: <i>наличными</i>"
    )
    # В группе отвечаем в личку, чтобы разговор о деньгах не шёл при всех.
    try:
        await callback.bot.send_message(callback.from_user.id, text)
        await callback.answer("Написал вам в личные сообщения.")
    except Exception:  # noqa: BLE001
        await callback.answer(
            "Не могу написать вам в личку — откройте бота и нажмите «Запустить».",
            show_alert=True,
        )
        await state.clear()


@router.message(StateFilter(ClaimPayment.receipt), F.photo | F.document | F.text)
async def pay_receipt(
    message: Message,
    state: FSMContext,
    bot: Bot,
    class_id: int,
    person: sqlite3.Row,
) -> None:
    data = await state.get_data()
    collection_id = int(data["collection_id"])
    amount = int(data["amount"])
    await state.clear()

    receipt_id = None
    method = "transfer"
    if message.photo:
        photo = message.photo[-1]
        receipt_id = files.save_tg(
            class_id,
            photo.file_id,
            kind="receipt",
            tg_unique_id=photo.file_unique_id,
            size=photo.file_size,
            uploaded_by=int(person["id"]),
        )
    elif message.document:
        receipt_id = files.save_tg(
            class_id,
            message.document.file_id,
            kind="receipt",
            tg_unique_id=message.document.file_unique_id,
            mime=message.document.mime_type,
            size=message.document.file_size,
            uploaded_by=int(person["id"]),
        )
    elif message.text and "налич" in message.text.lower():
        method = "cash"
    else:
        await state.set_state(ClaimPayment.receipt)
        await state.update_data(collection_id=collection_id, amount=amount)
        await message.answer("Нужен скриншот перевода или слово «наличными».")
        return

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
        await message.answer(f"Не отправил: {exc}.")
        return
    await message.answer("Спасибо! Передал казначею — он подтвердит.")
    await publisher.notify_treasurers(bot, payment_id)


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


# ── Отчёты ──────────────────────────────────────────────────────────────


@router.message(Command("касса", "money"))
async def cmd_balance(message: Message, class_id: int, roles: set[str]) -> None:
    if not roles_mod.sees_money(roles):
        await message.answer("Денежный раздел учителю не показывается.")
        return

    summary = money.summary(class_id)
    lines = [
        "💰 <b>Касса класса</b>",
        f"Остаток: <b>{util.money(summary['balance'], currency=True)}</b>",
        "",
        f"Поступило: {util.money(summary['inflow'])}",
        f"Потрачено: {util.money(summary['outflow'])}",
    ]

    open_collections = coll_svc.open_ones(class_id)
    if open_collections:
        lines.append("\n<b>Открытые сборы</b>")
        for collection in open_collections:
            progress = money.collection_progress(int(collection["id"]))
            lines.append(
                f"• {collection['title']} — "
                f"{util.money(progress['collected'])} из {util.money(progress['target'])} "
                f"({progress['percent']}%)"
            )

    recent = money.ledger(class_id, limit=5)
    if recent:
        lines.append("\n<b>Последние операции</b>")
        for entry in recent:
            sign = "+" if entry["direction"] == "in" else "−"
            lines.append(
                f"{sign}{util.money(entry['amount'])} · {entry['memo'] or entry['source_type']} "
                f"· {util.date_ru(entry['occurred_at'])}"
            )

    await message.answer("\n".join(lines))


@router.message(Command("мои", "mine"))
async def cmd_my_contributions(message: Message, person: sqlite3.Row | None) -> None:
    if person is None:
        await message.answer(texts.NOT_A_MEMBER)
        return

    rows = money.my_contributions(int(person["id"]))
    if not rows:
        await message.answer("Взносов пока нет.")
        return

    icons = {
        "confirmed": "✅",
        "partial": "🟡",
        "claimed": "⏳",
        "waived": "—",
        "pending": "⚪",
    }
    words = {
        "confirmed": "принят",
        "partial": "частично",
        "claimed": "ждёт подтверждения",
        "waived": "не требуется",
        "pending": "ожидается",
    }

    lines = ["<b>Мои взносы</b>", ""]
    for row in rows:
        status = money.contribution_status(row)
        due = f" · до {util.date_ru(row['due_date'])}" if row["due_date"] else ""
        lines.append(
            f"{icons[status]} {row['title']} — {util.money(row['expected'])} "
            f"({words[status]}){due}"
        )
    await message.answer("\n".join(lines))
