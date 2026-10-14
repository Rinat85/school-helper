"""Расходы в боте: карточка ревизору с кнопками, ответ автору, загрузка чека.

Сам расход вносится в Mini App. В группу о расходах бот не пишет ничего —
так решил комитет: расходы видны в приложении, в разделе «Расходы».
"""

from __future__ import annotations

import html
import sqlite3

from aiogram import Bot, F, Router
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from ..core import logger, util
from ..core import roles as roles_mod
from ..services import expenses as expense_svc
from ..storage import files, persons
from . import notify
from .middleware import deny

log = logger.get(__name__)
router = Router(name="expenses")


# ── Чек ─────────────────────────────────────────────────────────────────


async def upload_receipt(
    bot: Bot, class_id: int, person: sqlite3.Row, data: bytes, title: str
) -> int:
    """Чек из Mini App -> в личку автору -> file_id в базу.

    Файлы мы храним в Telegram по file_id (storage/files.py), а file_id выдаётся
    только на отправленное сообщение. Отправляем автору: заодно у него копия чека.
    """
    message = await bot.send_photo(
        person["tg_user_id"],
        BufferedInputFile(data, filename="receipt.jpg"),
        caption=f"🧾 Чек к расходу «{title}» — отправлен ревизору на утверждение.",
        disable_notification=True,
    )
    photo = message.photo[-1]
    return files.save_tg(
        class_id,
        photo.file_id,
        kind="photo",
        tg_unique_id=photo.file_unique_id,
        mime="image/jpeg",
        size=photo.file_size,
        uploaded_by=int(person["id"]),
    )


# ── Ревизору ────────────────────────────────────────────────────────────


def decision_keyboard(expense_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Утвердить", callback_data=f"econf:{expense_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"erej:{expense_id}"),
            ]
        ]
    )


def card(expense: sqlite3.Row, shortfall: int = 0) -> str:
    lines = [
        "💸 <b>Расход на утверждение</b>",
        f"{util.money(expense['amount'], currency=True)} · {html.escape(expense['title'])}",
        f"Потрачено {util.date_ru(expense['spent_at'])}, "
        f"вносит {html.escape(expense['created_by_name'] or '—')}",
    ]
    if expense["note"]:
        lines.append(f"📝 {html.escape(expense['note'])}")
    if shortfall:
        lines.append(f"⚠️ Касса уйдёт в минус на {util.money(shortfall, currency=True)}")
    return "\n".join(lines)


async def notify_auditors(bot: Bot, expense_id: int, shortfall: int = 0) -> int:
    """Карточка расхода с чеком — каждому ревизору в личку. Возвращает, скольким ушла."""
    expense = expense_svc.get(expense_id)
    if expense is None:
        return 0
    text = card(expense, shortfall)
    markup = decision_keyboard(expense_id)
    tg_file = files.tg_id(expense["receipt_id"])

    sent = 0
    for auditor in persons.with_role(int(expense["class_id"]), roles_mod.AUDITOR):
        if not auditor["dm_open"] or int(auditor["id"]) == int(expense["created_by"]):
            continue
        try:
            if tg_file:
                await bot.send_photo(
                    auditor["tg_user_id"], tg_file, caption=text, reply_markup=markup
                )
            else:
                await bot.send_message(auditor["tg_user_id"], text, reply_markup=markup)
            sent += 1
        except Exception:  # noqa: BLE001
            log.warning("cannot notify auditor %s about expense %s", auditor["id"], expense_id)
    return sent


def after_decision(expense: sqlite3.Row, reason: str | None = None) -> None:
    """Автору — чем кончилось."""
    if not expense["created_by"]:
        return
    author = persons.by_id(int(expense["created_by"]))
    if author is None or not author["dm_open"]:
        return
    what = f"{util.money(expense['amount'], currency=True)} · {html.escape(expense['title'])}"
    if expense["status"] == "approved":
        text = f"✅ Ревизор утвердил расход: {what}. Записан в кассу."
    else:
        text = f"❌ Ревизор отклонил расход: {what}."
        if reason:
            text += f"\nПричина: {html.escape(reason)}"
    notify.enqueue(int(author["id"]), f"expense.{expense['status']}", text)


# ── Кнопки ревизора ─────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("econf:"))
async def expense_approve(
    callback: CallbackQuery, person: sqlite3.Row | None, roles: set[str]
) -> None:
    await _decide(callback, person, roles, approve=True)


@router.callback_query(F.data.startswith("erej:"))
async def expense_reject(
    callback: CallbackQuery, person: sqlite3.Row | None, roles: set[str]
) -> None:
    await _decide(callback, person, roles, approve=False)


async def _decide(
    callback: CallbackQuery, person: sqlite3.Row | None, roles: set[str], *, approve: bool
) -> None:
    if person is None or not roles_mod.has(roles, "expense.approve"):
        await deny(callback, "expense.approve")
        return
    expense_id = int(callback.data.split(":")[1])
    class_id = int(person["class_id"])
    try:
        if approve:
            expense = expense_svc.approve(class_id, expense_id, int(person["id"]))
        else:
            expense = expense_svc.reject(class_id, expense_id, int(person["id"]), reason=None)
    except expense_svc.ExpenseError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await _strip_buttons(callback, "✅ Утверждено" if approve else "❌ Отклонено")
    await callback.answer("Записал в кассу." if approve else "Отклонил. Автору написал.")
    after_decision(expense)


async def _strip_buttons(callback: CallbackQuery, suffix: str) -> None:
    """Убирает кнопки, чтобы один расход нельзя было провести дважды."""
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
