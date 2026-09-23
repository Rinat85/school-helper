"""Всё, что бот пишет в группу и в личку по поводу денег.

Этим пользуются и хэндлеры бота, и API Mini App: сбор, созданный формой
в приложении, должен объявиться в группе точно так же, как созданный командой.
"""

from __future__ import annotations

import sqlite3

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..core import logger, util
from ..core import roles as roles_mod
from ..services import collections as coll_svc
from ..services import payments as pay_svc
from ..storage import db, files, money, persons
from ..storage import klass as klass_repo
from . import notify

log = logger.get(__name__)


def pay_keyboard(collection_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💵 Я оплатил", callback_data=f"pay:{collection_id}")]
        ]
    )


def decision_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"pconf:{payment_id}"),
                InlineKeyboardButton(text="❌ Не вижу", callback_data=f"prej:{payment_id}"),
            ]
        ]
    )


async def announce_collection(bot: Bot, collection_id: int) -> dict:
    """Объявление в группу + личное сообщение каждому. Возвращает, что получилось."""
    collection = coll_svc.get(collection_id)
    if collection is None:
        raise ValueError("collection not found")

    klass_row = klass_repo.get(int(collection["class_id"]))
    card = (klass_row["card_number"], klass_row["card_holder"]) if klass_row else (None, None)
    posted = False

    # 1. В группу — объявление без имён.
    if klass_row and klass_row["tg_chat_id"]:
        try:
            sent = await bot.send_message(
                klass_row["tg_chat_id"],
                coll_svc.public_announcement(collection, card),
                reply_markup=pay_keyboard(collection_id),
            )
        except Exception:  # noqa: BLE001 - бота выгнали из группы: сбор всё равно создан
            log.exception("cannot post collection %s to group", collection_id)
        else:
            posted = True
            db.execute(
                "UPDATE collection SET tg_message_id = ? WHERE id = ?",
                sent.message_id,
                collection_id,
            )
            try:
                await bot.pin_chat_message(klass_row["tg_chat_id"], sent.message_id)
            except Exception:  # noqa: BLE001 - нет права закреплять, не повод падать
                log.warning("cannot pin message in chat %s", klass_row["tg_chat_id"])

    # 2. Каждому лично — только его взнос.
    queued = notify.enqueue_many(
        persons.active(int(collection["class_id"])),
        "collection.new",
        coll_svc.private_reminder(collection),
        buttons=[[{"text": "💵 Я оплатил", "callback_data": f"pay:{collection_id}"}]],
    )
    return {"posted_to_group": posted, "queued": queued}


async def notify_treasurers(bot: Bot, payment_id: int) -> None:
    """Карточка заявки с чеком и кнопками — каждому казначею в личку."""
    payment = pay_svc.get(payment_id)
    collection = pay_svc.collection_of(payment_id)
    if payment is None or collection is None:
        return

    payer = persons.by_id(int(payment["person_id"]))
    how = "наличными" if payment["method"] == "cash" else "переводом"
    caption = (
        f"🧾 <b>{persons.label(payer)}</b> заявил оплату\n"
        f"{util.money(payment['amount'], currency=True)} {how} · {collection['title']}"
    )
    markup = decision_keyboard(payment_id)
    tg_file = files.tg_id(payment["receipt_id"])

    for treasurer in persons.with_role(int(collection["class_id"]), roles_mod.TREASURER):
        if not treasurer["dm_open"]:
            continue
        try:
            if tg_file:
                await bot.send_photo(
                    treasurer["tg_user_id"], tg_file, caption=caption, reply_markup=markup
                )
            else:
                await bot.send_message(treasurer["tg_user_id"], caption, reply_markup=markup)
        except Exception:  # noqa: BLE001
            log.warning("cannot notify treasurer %s", treasurer["id"])


async def after_confirm(bot: Bot, payment: sqlite3.Row) -> None:
    """Плательщику — «принято», в группе — обновлённая полоса."""
    collection = pay_svc.collection_of(int(payment["id"]))
    payer = persons.by_id(int(payment["person_id"]))
    if payer and payer["dm_open"]:
        notify.enqueue(
            int(payer["id"]),
            "payment.confirmed",
            f"✅ Взнос {util.money(payment['amount'], currency=True)} принят"
            + (f" — {collection['title']}" if collection else ""),
        )
    if collection:
        await refresh_progress(bot, collection)


def after_reject(payment: sqlite3.Row) -> None:
    payer = persons.by_id(int(payment["person_id"]))
    if payer and payer["dm_open"]:
        notify.enqueue(
            int(payer["id"]),
            "payment.rejected",
            "Казначей пока не нашёл ваш перевод. Проверьте, пожалуйста, "
            "и пришлите скриншот ещё раз — или свяжитесь с ним напрямую.",
        )


async def refresh_progress(bot: Bot, collection: sqlite3.Row) -> None:
    """Перерисовывает объявление о сборе в группе.

    Показываются только сумма и полоса. Ни имён, ни «17 из 24» — в классе на
    24 человека такая дробь выдаёт тех, кто не сдал, за минуту (SPEC §2).
    """
    collection = coll_svc.get(int(collection["id"])) or collection  # свежий статус
    klass_row = klass_repo.get(int(collection["class_id"]))
    if not klass_row or not klass_row["tg_chat_id"] or not collection["tg_message_id"]:
        return

    card = (klass_row["card_number"], klass_row["card_holder"])
    progress = money.collection_progress(int(collection["id"]))
    bar = util.progress_bar(progress["collected"], progress["target"])
    closed = collection["status"] != "open"
    body = (
        coll_svc.public_announcement(collection, card)
        + f"\n\n{bar}\n"
        + f"Собрано {util.money(progress['collected'])} "
        + f"из {util.money(progress['target'], currency=True)}"
        + ("\n\n🔒 Сбор закрыт" if closed else "")
    )
    try:
        await bot.edit_message_text(
            body,
            chat_id=klass_row["tg_chat_id"],
            message_id=collection["tg_message_id"],
            reply_markup=None if closed else pay_keyboard(int(collection["id"])),
        )
    except Exception:  # noqa: BLE001 - «message is not modified» и потерянные права
        log.debug("cannot refresh progress for collection %s", collection["id"])
    if closed:
        try:
            await bot.unpin_chat_message(
                klass_row["tg_chat_id"], message_id=collection["tg_message_id"]
            )
        except Exception:  # noqa: BLE001
            pass
