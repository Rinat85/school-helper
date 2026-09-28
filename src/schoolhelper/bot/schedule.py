"""Расписание от учительницы -> черновик -> предпросмотр председателю в личку.

Три входа:
  * учительница пишет в чате с учителем — бот читает сам (если знает, кто она);
  * председатель отвечает в том чате на сообщение командой /расписание;
  * председатель пересылает сообщение боту в личку.

Всё, что бот говорит в ответ, уходит в личку: в чат с учителем он не пишет
(это держит ещё и bot/guard.py).
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageOriginUser,
    WebAppInfo,
)

from ..core import logger, util
from ..core import roles as roles_mod
from ..services import schedule as schedule_svc
from ..services import schedule_parse
from ..storage import chats, persons
from ..storage import klass as klass_repo
from . import access, menu
from .middleware import deny, display_name_of

log = logger.get(__name__)
router = Router(name="schedule")

EDIT = "timetable.edit"


# ── Фильтры ─────────────────────────────────────────────────────────────


def _in_teacher_chat(message: Message) -> bool:
    row = chats.get(message.chat.id)
    return row is not None and row["role"] == chats.TEACHER


def _may_edit(roles: set[str]) -> bool:
    return roles_mod.has(roles, EDIT)


def _text_of(message: Message | None) -> str:
    if message is None:
        return ""
    return (message.text or message.caption or "").strip()


# ── Черновик и предпросмотр ─────────────────────────────────────────────


def _draft_from(
    class_id: int,
    text: str,
    *,
    source: str,
    chat_id: int,
    message_id: int,
    author_id: int | None,
    created_by: int | None,
) -> sqlite3.Row:
    """Один черновик на исходное сообщение: повторная команда его не плодит."""
    existing = schedule_svc.by_source(chat_id, message_id)
    if existing is not None:
        schedule_svc.reopen(int(existing["id"]))
        return schedule_svc.get(int(existing["id"]))

    today = util.today()
    parsed = schedule_parse.parse(text, today)
    draft_id = schedule_svc.create_draft(
        class_id,
        parsed,
        source=source,
        source_text=text,
        fallback_date=schedule_parse.next_school_day(today),
        source_chat_id=chat_id,
        source_message_id=message_id,
        source_user_id=author_id,
        created_by=created_by,
    )
    return schedule_svc.get(draft_id)


def preview_keyboard(draft: sqlite3.Row, *, offer_teacher: bool) -> InlineKeyboardMarkup:
    draft_id = int(draft["id"])
    rows: list[list[InlineKeyboardButton]] = []
    if draft["lessons"] != "[]" or draft["bring"] != "[]":
        rows.append(
            [InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"sch:pub:{draft_id}")]
        )
    url = menu.app_url()
    if url:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Исправить",
                    web_app=WebAppInfo(url=f"{url}/#/schedule/edit?draft={draft_id}"),
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="🚫 Это не расписание", callback_data=f"sch:no:{draft_id}")]
    )
    if offer_teacher:
        rows.append(
            [
                InlineKeyboardButton(
                    text="👩‍🏫 Автор — учительница, читать её сообщения самому",
                    callback_data=f"sch:teacher:{draft_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def preview_text(draft: sqlite3.Row) -> str:
    if draft["lessons"] == "[]" and draft["bring"] == "[]":
        return (
            "Не нашёл в сообщении уроков. Если это всё-таки расписание — "
            "заполните день вручную кнопкой «Исправить»."
        )
    head = "Распознал расписание. Проверьте и опубликуйте — родители увидят его в приложении.\n\n"
    return head + schedule_svc.render(draft)


async def send_preview(
    bot: Bot, tg_user_id: int, draft: sqlite3.Row, *, offer_teacher: bool = False
) -> bool:
    try:
        await bot.send_message(
            tg_user_id,
            preview_text(draft),
            reply_markup=preview_keyboard(draft, offer_teacher=offer_teacher),
        )
    except Exception:  # noqa: BLE001 - личка закрыта: черновик виден во вкладке «Расписание»
        log.warning("cannot send schedule preview to %s", tg_user_id)
        return False
    return True


# ── Чат с учителем ──────────────────────────────────────────────────────


@router.message(Command("учитель"), F.chat.type.in_(access.GROUP_TYPES), _in_teacher_chat)
async def mark_teacher(message: Message, bot: Bot, class_id: int, roles: set[str]) -> None:
    """Ответом на сообщение учительницы: «вот она, читай её сообщения»."""
    if not _may_edit(roles):
        return  # в этот чат не отвечаем даже отказом
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if target is None or target.is_bot:
        await _dm(bot, message.from_user.id, "Ответьте командой /учитель на сообщение учительницы.")
        return
    name = display_name_of(target)
    klass_repo.set_teacher(class_id, target.id, name)
    log.info("teacher of class %s set to %s", class_id, target.id)
    await _dm(
        bot,
        message.from_user.id,
        f"✅ Запомнил: учительница — {name}. Её сообщения с расписанием "
        "буду присылать вам на проверку.",
    )


@router.message(Command("расписание"), F.chat.type.in_(access.GROUP_TYPES), _in_teacher_chat)
async def force_schedule(
    message: Message, bot: Bot, class_id: int, person: sqlite3.Row | None, roles: set[str]
) -> None:
    """Ответом на сообщение: «это расписание, распознай»."""
    if not _may_edit(roles):
        return
    source = message.reply_to_message
    text = _text_of(source)
    if not text:
        await _dm(
            bot, message.from_user.id, "Ответьте командой /расписание на сообщение с расписанием."
        )
        return
    draft = _draft_from(
        class_id,
        text,
        source="teacher_chat",
        chat_id=message.chat.id,
        message_id=source.message_id,
        author_id=source.from_user.id if source.from_user else None,
        created_by=int(person["id"]) if person else None,
    )
    await send_preview(bot, message.from_user.id, draft)


@router.message(F.chat.type.in_(access.GROUP_TYPES), _in_teacher_chat, F.text | F.caption)
async def teacher_chat_message(message: Message, bot: Bot, class_id: int) -> None:
    """Сообщение учительницы: похоже на расписание — черновик председателям."""
    if message.from_user is None or not klass_repo.is_teacher(class_id, message.from_user.id):
        return
    text = _text_of(message)
    if schedule_parse.parse(text, util.today()) is None:
        return  # не расписание: объявления бот пока не разбирает
    if schedule_svc.by_source(message.chat.id, message.message_id) is not None:
        return
    draft = _draft_from(
        class_id,
        text,
        source="teacher_chat",
        chat_id=message.chat.id,
        message_id=message.message_id,
        author_id=message.from_user.id,
        created_by=None,
    )
    for chair in persons.with_role(class_id, roles_mod.CHAIR):
        if chair["tg_user_id"] and chair["dm_open"]:
            await send_preview(bot, int(chair["tg_user_id"]), draft)


# ── Пересылка в личку ───────────────────────────────────────────────────


def _forward_from_editor(message: Message, roles: set[str]) -> bool:
    return message.forward_origin is not None and bool(_text_of(message)) and _may_edit(roles)


@router.message(F.chat.type == "private", StateFilter(None), _forward_from_editor)
async def forwarded(message: Message, bot: Bot, class_id: int, person: sqlite3.Row) -> None:
    origin = message.forward_origin
    author_id = origin.sender_user.id if isinstance(origin, MessageOriginUser) else None
    draft = _draft_from(
        class_id,
        _text_of(message),
        source="forward",
        chat_id=message.chat.id,
        message_id=message.message_id,
        author_id=author_id,
        created_by=int(person["id"]),
    )
    klass_row = klass_repo.get(class_id)
    offer_teacher = (
        author_id is not None
        and not (isinstance(origin, MessageOriginUser) and origin.sender_user.is_bot)
        and klass_row is not None
        and klass_row["teacher_tg_user_id"] is None
    )
    await send_preview(bot, message.from_user.id, draft, offer_teacher=offer_teacher)


# ── Кнопки предпросмотра ────────────────────────────────────────────────


@router.callback_query(F.data.startswith("sch:"))
async def preview_decision(
    callback: CallbackQuery,
    bot: Bot,
    class_id: int,
    person: sqlite3.Row | None,
    roles: set[str],
) -> None:
    if person is None or not _may_edit(roles):
        await deny(callback, EDIT)
        return
    _, action, raw_id = callback.data.split(":")
    draft = schedule_svc.get(int(raw_id))
    if draft is None or int(draft["class_id"]) != class_id:
        await callback.answer("Черновик не найден.", show_alert=True)
        return

    if action == "teacher":
        name = await _teacher_name(bot, draft)
        klass_repo.set_teacher(class_id, int(draft["source_user_id"]), name)
        await callback.answer(f"Запомнил: учительница — {name}.", show_alert=True)
        if callback.message:
            keyboard = preview_keyboard(draft, offer_teacher=False)
            try:
                await callback.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:  # noqa: BLE001
                pass
        return

    try:
        if action == "pub":
            schedule_svc.publish(
                class_id,
                int(person["id"]),
                dt.date.fromisoformat(draft["date"]),
                schedule_svc.as_dict(draft)["lessons"],
                schedule_svc.as_dict(draft)["bring"],
                draft["note"],
                draft_id=int(draft["id"]),
            )
            outcome = "✅ Опубликовано — родители видят это во вкладке «Расписание»."
        else:
            schedule_svc.reject(class_id, int(person["id"]), int(draft["id"]))
            outcome = "🚫 Отмечено: не расписание."
    except schedule_svc.ScheduleError as exc:
        outcome = f"Не получилось: {exc}."

    if callback.message:
        body = schedule_svc.render(schedule_svc.get(int(draft["id"])))
        try:
            await callback.message.edit_text(f"{body}\n\n{outcome}", reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
    await callback.answer()


async def _teacher_name(bot: Bot, draft: sqlite3.Row) -> str:
    try:
        chat = await bot.get_chat(int(draft["source_user_id"]))
    except Exception:  # noqa: BLE001
        return "учительница"
    return " ".join(p for p in (chat.first_name, chat.last_name) if p) or "учительница"


async def _dm(bot: Bot, tg_user_id: int, text: str) -> None:
    try:
        await bot.send_message(tg_user_id, text)
    except Exception:  # noqa: BLE001
        log.warning("cannot DM %s", tg_user_id)
