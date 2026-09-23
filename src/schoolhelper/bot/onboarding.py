"""Подключение родителя: /setup в группе -> deep-link -> личка."""

from __future__ import annotations

import sqlite3

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from .. import config
from ..core import logger, security, util
from ..core import roles as roles_mod
from ..storage import db, persons
from ..storage import klass as klass_repo
from . import texts
from .middleware import display_name_of

log = logger.get(__name__)
router = Router(name="onboarding")


class Onboard(StatesGroup):
    name = State()
    child = State()


# ── /setup в группе ─────────────────────────────────────────────────────


@router.message(Command("setup"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_setup(message: Message, class_id: int, roles: set[str]) -> None:
    if not roles_mod.has(roles, "person.manage"):
        # Первичная настройка: если председателя ещё нет, разрешаем любому,
        # иначе привязать группу было бы некому.
        chair_exists = db.scalar(
            "SELECT 1 FROM person_role WHERE role = 'chair' AND revoked_at IS NULL"
        )
        if chair_exists:
            await message.answer(texts.NO_PERMISSION.format(roles="председатель"))
            return

    existing = klass_repo.by_chat(message.chat.id)
    if existing is None:
        klass_repo.bind_chat(class_id, message.chat.id)
        log.info("bound chat %s to class %s", message.chat.id, class_id)

    name = klass_repo.title(class_id)
    body = (
        texts.SETUP_GROUP.format(klass=name)
        if existing is None
        else texts.SETUP_ALREADY.format(klass=name)
    )
    await message.answer(
        body,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Подключиться", url=security.deeplink("join", class_id)
                    )
                ]
            ]
        ),
    )


# ── /start ──────────────────────────────────────────────────────────────


@router.message(CommandStart(deep_link=True))
async def start_with_payload(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    class_id: int,
    person: sqlite3.Row | None,
) -> None:
    parsed = security.verify_deeplink_payload(command.args or "")
    if parsed is None or parsed[0] != "join":
        await message.answer("Ссылка недействительна. Попросите председателя прислать новую.")
        return

    target_class = int(parsed[1])
    await _begin(message, state, target_class, person)


@router.message(CommandStart(deep_link=False))
async def start_plain(
    message: Message, state: FSMContext, class_id: int, person: sqlite3.Row | None
) -> None:
    if person is None:
        # Председатель из .env заходит без приглашения: на первом запуске
        # пригласить его некому, а ходить ради этого в группу — тупик.
        if config.BOOTSTRAP_CHAIR_TG_ID and message.from_user.id == config.BOOTSTRAP_CHAIR_TG_ID:
            await _begin(message, state, class_id, None)
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


async def _begin(
    message: Message, state: FSMContext, class_id: int, person: sqlite3.Row | None
) -> None:
    user = message.from_user
    row = persons.upsert_from_tg(
        class_id,
        user.id,
        display_name=display_name_of(user),
        tg_username=user.username,
    )
    await message.answer(texts.WELCOME.format(klass=klass_repo.title(class_id)))

    if person is not None and person["child_name"]:
        # Повторный /start у уже заполненного профиля — анкету не гоняем.
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

    await message.answer(
        texts.ONBOARD_DONE.format(name=name, klass=klass_repo.title(class_id)),
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(texts.PRIVACY_NOTICE)
    log.info("person %s onboarded in class %s", person_id, class_id)


# ── Помощь и служебное ──────────────────────────────────────────────────


@router.message(Command("help", "помощь"))
async def cmd_help(message: Message, roles: set[str]) -> None:
    await _help(message, roles)


async def _help(message: Message, roles: set[str]) -> None:
    body = texts.HELP_PARENT
    if roles & {roles_mod.TREASURER, roles_mod.CHAIR, roles_mod.AUDITOR, roles_mod.ADMIN}:
        body += texts.HELP_COMMITTEE
    await message.answer(body)


@router.message(Command("кто", "who"))
async def cmd_who(message: Message, class_id: int, roles: set[str]) -> None:
    """Кому бот физически не может написать (SPEC §5.5)."""
    if not roles_mod.has(roles, "person.manage"):
        await message.answer(texts.NO_PERMISSION.format(roles="председатель"))
        return

    total = persons.count_active(class_id)
    pending = persons.not_connected(class_id)
    if not pending:
        await message.answer(f"Все {total} подключились — уведомления дойдут до всех.")
        return

    names = "\n".join(f"• {persons.label(p)}" for p in pending)
    word = util.plural(len(pending), "родитель", "родителя", "родителей")
    await message.answer(
        f"<b>Не подключили бота: {len(pending)} из {total}</b>\n\n{names}\n\n"
        f"{word.capitalize()} не получит напоминания о взносах и объявления. "
        f"Пришлите им ссылку: {security.deeplink('join', class_id)}"
    )
