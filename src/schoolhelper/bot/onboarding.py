"""Подключение родителя: /setup в группе -> deep-link -> личка."""

from __future__ import annotations

import html
import sqlite3

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from .. import config
from ..core import logger, security
from ..core import roles as roles_mod
from ..services import people as people_svc
from ..storage import chats, persons
from ..storage import klass as klass_repo
from . import access, menu, publisher, texts
from .middleware import deny, display_name_of

log = logger.get(__name__)
router = Router(name="onboarding")


class Onboard(StatesGroup):
    name = State()
    child = State()


# ── /setup в группе ─────────────────────────────────────────────────────


@router.message(Command("setup"), F.chat.type.in_(access.GROUP_TYPES))
async def cmd_setup(message: Message, bot: Bot, class_id: int, roles: set[str]) -> None:
    """Роль чата спрашиваем в личке: чат может оказаться чатом с учителем,
    а туда бот не пишет даже вопрос «что это за чат»."""
    if not access.may_bind_group(message.from_user.id, roles):
        if not chats.is_read_only(message.chat.id):
            await message.answer("Привязать группу к классу может только председатель.")
        return

    current = chats.get(message.chat.id)
    body = texts.SETUP_ASK.format(
        title=html.escape(message.chat.title or "без названия"),
        klass=klass_repo.title(class_id),
        current=texts.SETUP_CURRENT.format(role=chats.RU[current["role"]]) if current else "",
    )
    try:
        await bot.send_message(
            message.from_user.id, body, reply_markup=_chat_role_keyboard(message.chat.id)
        )
    except Exception:  # noqa: BLE001 - личка закрыта: в группу не пишем, это может быть чат с учителем
        log.warning("cannot ask %s about chat %s role", message.from_user.id, message.chat.id)


def _chat_role_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=chats.RU[role], callback_data=f"bind:{role}:{chat_id}")
                for role in chats.ROLES
            ]
        ]
    )


def _join_keyboard(class_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подключиться", url=security.deeplink("join", class_id))]
        ]
    )


@router.callback_query(F.data.startswith("bind:"))
async def bind_chat_role(
    callback: CallbackQuery, bot: Bot, class_id: int, roles: set[str]
) -> None:
    if not access.may_bind_group(callback.from_user.id, roles):
        await deny(callback, "person.manage")
        return
    _, role, raw_chat = callback.data.split(":")
    chat_id = int(raw_chat)
    if role not in chats.ROLES:
        await callback.answer()
        return
    try:
        chat = await bot.get_chat(chat_id)
    except Exception:  # noqa: BLE001
        await callback.answer("Меня уже нет в этой группе.", show_alert=True)
        return

    title = chat.title or "без названия"
    chats.bind(class_id, chat_id, role, title, callback.from_user.id)
    log.info("chat %s bound to class %s as %s", chat_id, class_id, role)

    klass = klass_repo.title(class_id)
    if role == chats.TEACHER:
        done = texts.SETUP_TEACHER_DONE.format(title=html.escape(title))
    else:
        done = f"✅ «{html.escape(title)}» подключён: {chats.RU[role].lower()} чат."
        try:
            if role == chats.PARENTS:
                await bot.send_message(
                    chat_id, texts.SETUP_GROUP.format(klass=klass),
                    reply_markup=_join_keyboard(class_id),
                )
            else:
                await bot.send_message(chat_id, texts.SETUP_COMMITTEE.format(klass=klass))
        except Exception:  # noqa: BLE001 - нет права писать: привязка всё равно сделана
            log.warning("cannot post setup message to chat %s", chat_id)

    if callback.message:
        try:
            await callback.message.edit_text(done, reply_markup=None)
        except Exception:  # noqa: BLE001
            await bot.send_message(callback.from_user.id, done)
    await callback.answer()


# ── /start ──────────────────────────────────────────────────────────────


@router.message(Command("setup"), F.chat.type == "private")
async def cmd_setup_private(message: Message) -> None:
    await message.answer(
        "Эту команду нужно написать в самой группе класса — после того, как добавите "
        "туда бота администратором."
    )


@router.message(CommandStart(deep_link=True), F.chat.type == "private")
async def start_with_payload(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    class_id: int,
) -> None:
    parsed = security.verify_deeplink_payload(command.args or "")
    if parsed is None or parsed[0] != "join":
        await message.answer("Ссылка недействительна. Попросите председателя прислать новую.")
        return

    target_class = int(parsed[1])
    await _begin(message, state, target_class)


@router.message(CommandStart(deep_link=False), F.chat.type == "private")
async def start_plain(
    message: Message,
    state: FSMContext,
    class_id: int,
    person: sqlite3.Row | None,
    candidate: sqlite3.Row | None,
) -> None:
    if person is None:
        if persons.is_pending(candidate):
            await message.answer(texts.PENDING_WAIT)
            return
        # Председатель из .env заходит без приглашения: на первом запуске
        # пригласить его некому, а ходить ради этого в группу — тупик.
        if config.BOOTSTRAP_CHAIR_TG_ID and message.from_user.id == config.BOOTSTRAP_CHAIR_TG_ID:
            await _begin(message, state, class_id)
            return
        await message.answer(texts.NOT_A_MEMBER)
        return
    # Уже в классе — просто отмечаем, что личка открыта, и показываем помощь.
    persons.upsert_from_tg(
        class_id,
        message.from_user.id,
        display_name=person["display_name"],
        tg_username=message.from_user.username,
    )
    await state.clear()
    await _help(message, roles_mod.roles_of(int(person["id"])))


async def _begin(message: Message, state: FSMContext, class_id: int) -> None:
    """Новенький по ссылке заводится в статусе «ждёт одобрения» (кроме председателя
    из .env) и заполняет анкету; по её окончании председателю уходит заявка."""
    user = message.from_user
    row = persons.upsert_from_tg(
        class_id,
        user.id,
        display_name=display_name_of(user),
        tg_username=user.username,
    )
    await message.answer(texts.WELCOME.format(klass=klass_repo.title(class_id)))

    if persons.is_member(row) and row["child_name"]:
        # Повторный переход по ссылке у своего — анкету не гоняем.
        await _help(message, roles_mod.roles_of(int(row["id"])))
        return

    suggested = display_name_of(user)
    await state.set_state(Onboard.name)
    await state.update_data(person_id=int(row["id"]), class_id=class_id)
    await message.answer(
        texts.ASK_NAME,
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=suggested)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )


@router.message(StateFilter(Onboard.name), F.text)
async def got_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()[:64]
    if not name:
        await message.answer("Не понял имя, попробуйте ещё раз.")
        return
    await state.update_data(display_name=name)
    await state.set_state(Onboard.child)
    await message.answer(texts.ASK_CHILD, reply_markup=ReplyKeyboardRemove())


@router.message(StateFilter(Onboard.child), Command("skip"))
async def skip_child(message: Message, state: FSMContext) -> None:
    await _finish(message, state, child=None)


@router.message(StateFilter(Onboard.child), F.text)
async def got_child(message: Message, state: FSMContext) -> None:
    await _finish(message, state, child=message.text.strip()[:64] or None)


async def _finish(message: Message, state: FSMContext, child: str | None) -> None:
    data = await state.get_data()
    person_id = int(data["person_id"])
    class_id = int(data["class_id"])
    name = data.get("display_name") or display_name_of(message.from_user)

    persons.set_names(person_id, name, child)
    await state.clear()

    row = persons.by_id(person_id)
    if persons.is_pending(row):
        await message.answer(
            texts.PENDING_THANKS.format(name=name), reply_markup=ReplyKeyboardRemove()
        )
        await publisher.notify_join_request(message.bot, row)
        log.info("person %s asked to join class %s", person_id, class_id)
        return

    await message.answer(
        texts.ONBOARD_DONE.format(name=name, klass=klass_repo.title(class_id)),
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(texts.PRIVACY_NOTICE, reply_markup=menu.app_keyboard())
    log.info("person %s onboarded in class %s", person_id, class_id)


# ── Помощь ──────────────────────────────────────────────────────────────
# Команд, кроме /start, у бота нет: всё остальное — в приложении класса.
# /help не показывается в меню, но срабатывает — его набирают по привычке.


@router.message(Command("help", "помощь"), F.chat.type == "private")
async def cmd_help(
    message: Message,
    person: sqlite3.Row | None,
    candidate: sqlite3.Row | None,
    roles: set[str],
) -> None:
    if person is None:
        await message.answer(
            texts.PENDING_WAIT if persons.is_pending(candidate) else texts.NOT_A_MEMBER
        )
        return
    await _help(message, roles)


# ── Решение по заявке ───────────────────────────────────────────────────


@router.callback_query(F.data.startswith("join:"))
async def join_decision(
    callback: CallbackQuery, class_id: int, person: sqlite3.Row | None, roles: set[str]
) -> None:
    """Кнопки «Впустить / Отклонить» из карточки заявки у председателя."""
    if person is None or not roles_mod.has(roles, "person.manage"):
        await deny(callback, "person.manage")
        return

    _, verdict, raw_id = callback.data.split(":")
    target_id = int(raw_id)
    try:
        if verdict == "ok":
            enrolled = people_svc.approve(class_id, int(person["id"]), target_id)
            publisher.after_approve(persons.by_id(target_id), enrolled)
            outcome = "✅ Впущен(а)"
            if enrolled:
                outcome += " · добавлен(а) в идущие сборы: " + ", ".join(
                    f"«{c['title']}»" for c in enrolled
                )
        else:
            people_svc.decline(class_id, int(person["id"]), target_id)
            publisher.after_decline(persons.by_id(target_id))
            outcome = "✖️ Отклонено"
    except people_svc.PeopleError as exc:
        # Второй председатель мог уже решить — или решили во вкладке «Люди».
        outcome = f"Уже рассмотрено: {exc}"

    if callback.message:
        try:
            await callback.message.edit_text(
                f"{callback.message.html_text}\n\n{outcome}", reply_markup=None
            )
        except Exception:  # noqa: BLE001
            pass
    await callback.answer()


async def _help(message: Message, roles: set[str]) -> None:
    committee = bool(roles & {roles_mod.TREASURER, roles_mod.CHAIR, roles_mod.AUDITOR})
    keyboard = menu.app_keyboard()
    if keyboard is None:
        await message.answer(texts.HOME_NO_APP)
        return
    body = texts.HOME + (texts.HOME_COMMITTEE if committee else "")
    await message.answer(body, reply_markup=keyboard)
