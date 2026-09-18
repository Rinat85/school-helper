"""Сборы и платежи в боте: /сбор, «Я оплатил», подтверждение казначеем, /касса."""

from __future__ import annotations

import sqlite3

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..core import logger, util
from ..core import roles as roles_mod
from ..services import collections as coll_svc
from ..services import payments as pay_svc
from ..storage import db, files, money, persons
from ..storage import klass as klass_repo
from . import notify, texts
from .middleware import deny

log = logger.get(__name__)
router = Router(name="money")


class NewCollection(StatesGroup):
    title = State()
    amount = State()
    due = State()
    confirm = State()


class ClaimPayment(StatesGroup):
    receipt = State()


# ── Создание сбора ──────────────────────────────────────────────────────


@router.message(Command("сбор", "collect"))
async def cmd_new_collection(
    message: Message, state: FSMContext, person: sqlite3.Row | None, roles: set[str]
) -> None:
    if person is None:
        await message.answer(texts.NOT_A_MEMBER)
        return
    if not roles_mod.has(roles, "collection.create"):
        await deny(message, "collection.create")
        return

    await state.set_state(NewCollection.title)
    await message.answer(
        "Новый сбор. На что собираем?\n\nНапример: <i>Подарки на Новый год</i>\n\n"
        "Отмена: /cancel"
    )


@router.message(Command("cancel", "отмена"), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("Отменено.")


@router.message(StateFilter(NewCollection.title), F.text)
async def collection_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip()[:120])
    await state.set_state(NewCollection.amount)
    await message.answer("Сколько с человека? Только число, в сумах.\n\nНапример: <i>50000</i>")


@router.message(StateFilter(NewCollection.amount), F.text)
async def collection_amount(message: Message, state: FSMContext) -> None:
    raw = message.text.replace(" ", "").replace(" ", "").replace(",", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Нужно целое число, например 50000. Попробуйте ещё раз.")
        return
    await state.update_data(amount=int(raw))
    await state.set_state(NewCollection.due)
    await message.answer(
        "До какого числа собираем?\n\nНапример: <i>15.12</i> или <i>15 декабря</i>\n\n"
        "Без срока: /skip"
    )


@router.message(StateFilter(NewCollection.due), Command("skip"))
async def collection_no_due(message: Message, state: FSMContext, class_id: int) -> None:
    await _preview(message, state, class_id, due=None)


@router.message(StateFilter(NewCollection.due), F.text)
async def collection_due(message: Message, state: FSMContext, class_id: int) -> None:
    due = util.parse_date_ru(message.text)
    if due is None:
        await message.answer("Не понял дату. Напишите как 15.12 или «15 декабря».")
        return
    await _preview(message, state, class_id, due=due.isoformat())


async def _preview(
    message: Message, state: FSMContext, class_id: int, due: str | None
) -> None:
    data = await state.get_data()
    await state.update_data(due=due)
    await state.set_state(NewCollection.confirm)

    people = persons.count_active(class_id)
    total = int(data["amount"]) * people
    offline = len(persons.not_connected(class_id))

    lines = [
        f"<b>{data['title']}</b>",
        f"По {util.money(data['amount'], currency=True)} × {people} "
        f"{util.plural(people, 'родитель', 'родителя', 'родителей')}",
        f"Итого: {util.money(total, currency=True)}",
    ]
    if due:
        lines.append(f"Срок: до {util.date_ru(due)}")
    if offline:
        lines.append(
            f"\n⚠️ {offline} "
            f"{util.plural(offline, 'родитель', 'родителя', 'родителей')} не подключили бота — "
            f"им придётся сказать лично (/кто)"
        )

    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Объявить сбор", callback_data="coll:publish")],
                [InlineKeyboardButton(text="✖️ Отмена", callback_data="coll:cancel")],
            ]
        ),
    )


@router.callback_query(F.data == "coll:cancel")
async def collection_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()


@router.callback_query(F.data == "coll:publish", StateFilter(NewCollection.confirm))
async def collection_publish(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    class_id: int,
    person: sqlite3.Row,
    roles: set[str],
) -> None:
    if not roles_mod.has(roles, "collection.create"):
        await deny(callback, "collection.create")
        return

    data = await state.get_data()
    await state.clear()

    collection_id = coll_svc.create(
        class_id,
        int(person["id"]),
        title=data["title"],
        amount_per_person=int(data["amount"]),
        due_date=data.get("due"),
    )
    coll_svc.open_(collection_id, int(person["id"]))
    collection = coll_svc.get(collection_id)
    assert collection is not None

    klass_row = klass_repo.get(class_id)
    card = (klass_row["card_number"], klass_row["card_holder"]) if klass_row else (None, None)

    # 1. В группу — объявление и прогресс без имён.
    if klass_row and klass_row["tg_chat_id"]:
        sent = await bot.send_message(
            klass_row["tg_chat_id"],
            coll_svc.public_announcement(collection, card),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💵 Я оплатил", callback_data=f"pay:{collection_id}"
                        )
                    ]
                ]
            ),
        )
        db.execute(
            "UPDATE collection SET tg_message_id = ? WHERE id = ?", sent.message_id, collection_id
        )
        try:
            await bot.pin_chat_message(klass_row["tg_chat_id"], sent.message_id)
        except Exception:  # noqa: BLE001 - нет прав на закрепление, не повод падать
            log.warning("cannot pin message in chat %s", klass_row["tg_chat_id"])

    # 2. Каждому лично — только его взнос.
    queued = notify.enqueue_many(
        persons.active(class_id),
        "collection.new",
        coll_svc.private_reminder(collection),
        buttons=[[{"text": "💵 Я оплатил", "callback_data": f"pay:{collection_id}"}]],
    )

    await callback.message.edit_text(
        f"✅ Сбор объявлен. Код для переводов: <code>{collection['payment_code']}</code>\n"
        f"Личных сообщений в очереди: {queued}"
    )
    await callback.answer()


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

    await state.set_state(ClaimPayment.receipt)
    await state.update_data(collection_id=collection_id, amount=int(contribution["expected"]))

    text = (
        f"<b>{collection['title']}</b>\n"
        f"Ваш взнос: {util.money(contribution['expected'], currency=True)}\n\n"
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

    payment_id = pay_svc.claim(
        int(person["id"]),
        collection_id,
        amount=amount,
        method=method,
        receipt_file_id=receipt_id,
    )
    await message.answer("Спасибо! Передал казначею — он подтвердит.")
    await _notify_treasurers(bot, class_id, payment_id, person, amount, method, receipt_id)


async def _notify_treasurers(
    bot: Bot,
    class_id: int,
    payment_id: int,
    payer: sqlite3.Row,
    amount: int,
    method: str,
    receipt_id: int | None,
) -> None:
    collection = pay_svc.collection_of(payment_id)
    title = collection["title"] if collection else ""
    how = "наличными" if method == "cash" else "переводом"
    caption = (
        f"🧾 <b>{persons.label(payer)}</b> заявил оплату\n"
        f"{util.money(amount, currency=True)} {how} · {title}"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"pconf:{payment_id}"),
                InlineKeyboardButton(text="❌ Не вижу", callback_data=f"prej:{payment_id}"),
            ]
        ]
    )

    tg_file = files.tg_id(receipt_id)
    for treasurer in persons.with_role(class_id, roles_mod.TREASURER):
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
    collection = pay_svc.collection_of(payment_id)

    await _strip_buttons(callback, f"✅ Подтверждено · {persons.label(payer)}")
    await callback.answer("Записал в кассу.")

    if payer and payer["dm_open"]:
        notify.enqueue(
            int(payer["id"]),
            "payment.confirmed",
            f"✅ Взнос {util.money(payment['amount'], currency=True)} принят"
            + (f" — {collection['title']}" if collection else ""),
        )
    if collection:
        await _refresh_progress(bot, collection)


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

    if payer and payer["dm_open"]:
        notify.enqueue(
            int(payer["id"]),
            "payment.rejected",
            "Казначей пока не нашёл ваш перевод. Проверьте, пожалуйста, "
            "и пришлите скриншот ещё раз — или свяжитесь с ним напрямую.",
        )


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


async def _refresh_progress(bot: Bot, collection: sqlite3.Row) -> None:
    """Перерисовывает закреплённое сообщение в группе.

    Показываются только сумма и полоса. Ни имён, ни «17 из 24» — в классе на
    24 человека такая дробь выдаёт тех, кто не сдал, за минуту (SPEC §2).
    """
    klass_row = klass_repo.get(int(collection["class_id"]))
    if not klass_row or not klass_row["tg_chat_id"] or not collection["tg_message_id"]:
        return

    card = (klass_row["card_number"], klass_row["card_holder"])
    progress = money.collection_progress(int(collection["id"]))
    bar = util.progress_bar(progress["collected"], progress["target"])
    body = (
        coll_svc.public_announcement(collection, card)
        + f"\n\n{bar}\n"
        + f"Собрано {util.money(progress['collected'])} "
        + f"из {util.money(progress['target'], currency=True)}"
    )
    markup = (
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💵 Я оплатил", callback_data=f"pay:{collection['id']}"
                    )
                ]
            ]
        )
        if collection["status"] == "open"
        else None
    )
    try:
        await bot.edit_message_text(
            body,
            chat_id=klass_row["tg_chat_id"],
            message_id=collection["tg_message_id"],
            reply_markup=markup,
        )
    except Exception:  # noqa: BLE001 - «message is not modified» и потерянные права
        log.debug("cannot refresh progress for collection %s", collection["id"])


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


@router.message(Command("очередь", "queue"))
async def cmd_pending(message: Message, class_id: int, roles: set[str]) -> None:
    """Очередь казначея — кто заявил оплату и ждёт подтверждения."""
    if not roles_mod.has(roles, "payment.confirm"):
        await deny(message, "payment.confirm")
        return

    rows = pay_svc.pending(class_id)
    if not rows:
        await message.answer("Очередь пуста — всё подтверждено.")
        return

    for row in rows:
        await message.answer(
            f"🧾 <b>{row['display_name']}</b> · {util.money(row['amount'], currency=True)}\n"
            f"{row['collection_title']}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Подтвердить", callback_data=f"pconf:{row['id']}"
                        ),
                        InlineKeyboardButton(
                            text="❌ Не вижу", callback_data=f"prej:{row['id']}"
                        ),
                    ]
                ]
            ),
        )
