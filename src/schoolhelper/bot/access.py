"""Где бот работает и кто может привязать группу.

Бот публичный: добавить его в любую группу может кто угодно. Поэтому
  * привязать группу к классу может только председатель — или тот, кто им
    станет по BOOTSTRAP_CHAIR_TG_ID, пока председателя ещё нет;
  * из любой другой группы бот сразу выходит — иначе родитель мог бы позвать
    его в посторонний чат и вывести туда что-нибудь из кассы класса.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import ChatMemberUpdated

from .. import config
from ..core import logger
from ..core import roles as roles_mod
from ..storage import klass as klass_repo

log = logger.get(__name__)
router = Router(name="access")

GROUP_TYPES = {"group", "supergroup"}


def may_bind_group(tg_user_id: int, roles: set[str]) -> bool:
    """Привязать группу к классу: председатель или будущий председатель из .env.

    Раньше при отсутствии председателя это мог сделать любой — открытое окно
    сразу после первого запуска или очистки базы.
    """
    if roles_mod.has(roles, "person.manage"):
        return True
    return bool(config.BOOTSTRAP_CHAIR_TG_ID) and tg_user_id == config.BOOTSTRAP_CHAIR_TG_ID


def may_stay(
    chat_id: int, bound_chat_id: int | None, adder_tg_id: int, adder_roles: set[str]
) -> bool:
    """Оставаться в группе можно, если это группа класса или бота добавил председатель.

    Председателю можно добавить бота в новую группу — например, при переезде
    класса в другой чат; привязку он сделает потом командой /setup.
    """
    if bound_chat_id is not None and chat_id == bound_chat_id:
        return True
    return may_bind_group(adder_tg_id, adder_roles)


@router.my_chat_member(F.chat.type.in_(GROUP_TYPES))
async def added_to_group(
    event: ChatMemberUpdated, bot: Bot, class_id: int, roles: set[str]
) -> None:
    if event.new_chat_member.status not in ("member", "administrator"):
        return  # бота удалили или ограничили — делать нечего

    klass_row = klass_repo.get(class_id)
    bound = klass_row["tg_chat_id"] if klass_row else None
    if may_stay(event.chat.id, bound, event.from_user.id, roles):
        return

    log.warning("left foreign group %s (added by %s)", event.chat.id, event.from_user.id)
    try:
        await bot.send_message(
            event.chat.id, "Этот бот работает только в группе своего класса и сюда не подключается."
        )
    except Exception:  # noqa: BLE001 - написать могли и не дать, уйти всё равно надо
        pass
    await bot.leave_chat(event.chat.id)
