"""Сборы: создание, роспись по родителям, закрытие.

Слой сервисов не знает ни про Telegram, ни про HTTP — им пользуется и бот,
и API Mini App (этап 2). Здесь же живут правила, которые нельзя нарушать
из интерфейса: см. SPEC §2 (никаких публичных списков должников).
"""

from __future__ import annotations

import sqlite3

from .. import config
from ..core import util
from ..storage import db, journal, money, persons


def payment_code(collection_id: int) -> str:
    """Код для комментария к переводу: '1V-07'. Нужен для сверки (SPEC §5.1)."""
    return f"{config.CLASS_CODE}-{collection_id:02d}"


def create(
    class_id: int,
    actor_id: int,
    *,
    title: str,
    amount_per_person: int,
    due_date: str | None = None,
    purpose: str | None = None,
    case_id: int | None = None,
) -> int:
    """Создаёт сбор в статусе draft и расписывает его по активным родителям."""
    with db.tx():
        collection_id = db.insert(
            "collection",
            class_id=class_id,
            case_id=case_id,
            title=title,
            purpose=purpose,
            amount_per_person=amount_per_person,
            due_date=due_date,
            status="draft",
            created_by=actor_id,
            created_at=util.now_iso(),
        )
        db.execute(
            "UPDATE collection SET payment_code = ? WHERE id = ?",
            payment_code(collection_id),
            collection_id,
        )
        for person in persons.active(class_id):
            db.insert(
                "contribution",
                collection_id=collection_id,
                person_id=int(person["id"]),
                expected=amount_per_person,
            )
        journal.audit(
            class_id,
            actor_id,
            "collection.create",
            object_type="collection",
            object_id=collection_id,
            after={"title": title, "amount_per_person": amount_per_person},
        )
    return collection_id


def open_(collection_id: int, actor_id: int) -> None:
    row = get(collection_id)
    if row is None:
        raise ValueError("collection not found")
    with db.tx():
        db.execute("UPDATE collection SET status = 'open' WHERE id = ?", collection_id)
        journal.log_case(
            row["case_id"],
            "collection_opened",
            actor_id=actor_id,
            ref_type="collection",
            ref_id=collection_id,
            text=f"Сбор открыт — {util.money(row['amount_per_person'])} с человека",
        )


def close(collection_id: int, actor_id: int) -> None:
    row = get(collection_id)
    if row is None:
        raise ValueError("collection not found")
    progress = money.collection_progress(collection_id)
    with db.tx():
        db.execute(
            "UPDATE collection SET status = 'closed', closed_at = ? WHERE id = ?",
            util.now_iso(),
            collection_id,
        )
        journal.log_case(
            row["case_id"],
            "collection_closed",
            actor_id=actor_id,
            ref_type="collection",
            ref_id=collection_id,
            text=(
                f"Сбор закрыт — {util.money(progress['collected'])} "
                f"из {util.money(progress['target'])}"
            ),
        )
        journal.audit(
            row["class_id"],
            actor_id,
            "collection.close",
            object_type="collection",
            object_id=collection_id,
            after=progress,
        )


def get(collection_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM collection WHERE id = ?", collection_id)


def open_ones(class_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT * FROM collection WHERE class_id = ? AND status = 'open' ORDER BY created_at",
        class_id,
    )


def contribution_of(collection_id: int, person_id: int) -> sqlite3.Row | None:
    return db.one(
        "SELECT * FROM contribution WHERE collection_id = ? AND person_id = ?",
        collection_id,
        person_id,
    )


def enroll_in_open(class_id: int, person_id: int) -> list[sqlite3.Row]:
    """Новый участник в уже идущих сборах — с той же суммой, что у остальных.

    Иначе одобренный родитель видит сбор в приложении, а «Я оплатил» отвечает
    «вас нет в списке». Если платить ему не нужно — казначей освободит.
    """
    enrolled = []
    for collection in open_ones(class_id):
        if contribution_of(int(collection["id"]), person_id) is not None:
            continue
        db.insert(
            "contribution",
            collection_id=int(collection["id"]),
            person_id=person_id,
            expected=int(collection["amount_per_person"]),
        )
        enrolled.append(collection)
    return enrolled


def set_waived(
    collection_id: int, person_id: int, actor_id: int, waived: bool, note: str | None = None
) -> None:
    """Освобождение от взноса — тихое, без статуса «должник» (SPEC §2)."""
    db.execute(
        "UPDATE contribution SET waived = ?, note = ? WHERE collection_id = ? AND person_id = ?",
        int(waived),
        note,
        collection_id,
        person_id,
    )
    row = get(collection_id)
    if row:
        journal.audit(
            row["class_id"],
            actor_id,
            "contribution.waive" if waived else "contribution.unwaive",
            object_type="collection",
            object_id=collection_id,
            after={"person_id": person_id},
        )


def waive(collection_id: int, person_id: int, actor_id: int, note: str | None = None) -> None:
    set_waived(collection_id, person_id, actor_id, True, note)


def listing(class_id: int) -> list[sqlite3.Row]:
    """Открытые сначала, внутри — свежие сверху."""
    return db.query(
        "SELECT * FROM collection WHERE class_id = ? AND status IN ('open', 'closed') "
        "ORDER BY status = 'open' DESC, created_at DESC",
        class_id,
    )


# ── Тексты ──────────────────────────────────────────────────────────────


def public_announcement(collection: sqlite3.Row, card: tuple[str | None, str | None]) -> str:
    """Сообщение в группу. Имён и «сколько из скольких» здесь нет — это принцип."""
    lines = [
        f"💰 <b>Новый сбор: {collection['title']}</b>",
        f"По {util.money(collection['amount_per_person'], currency=True)} с человека",
    ]
    if collection["due_date"]:
        lines.append(f"Срок: до {util.date_ru(collection['due_date'])}")
    if collection["purpose"]:
        lines.append(f"\n{collection['purpose']}")

    number, holder = card
    if number:
        lines.append(f"\n💳 <code>{number}</code>" + (f" ({holder})" if holder else ""))
    if collection["payment_code"]:
        lines.append(
            f"При переводе укажите код: <code>{collection['payment_code']}</code>"
        )
    lines.append("\nВзнос добровольный.")
    return "\n".join(lines)


def progress_text(collection: sqlite3.Row) -> str:
    """Прогресс для закреплённого сообщения: сумма и полоса, без имён."""
    progress = money.collection_progress(collection["id"])
    bar = util.progress_bar(progress["collected"], progress["target"])
    status = " · закрыт" if collection["status"] == "closed" else ""
    return (
        f"💰 <b>{collection['title']}</b>{status}\n"
        f"{bar}\n"
        f"{util.money(progress['collected'])} из {util.money(progress['target'], currency=True)}"
    )


def private_reminder(collection: sqlite3.Row) -> str:
    due = f" до {util.date_ru(collection['due_date'])}" if collection["due_date"] else ""
    code = (
        f"\nКод для комментария к переводу: <code>{collection['payment_code']}</code>"
        if collection["payment_code"]
        else ""
    )
    return (
        f"💰 <b>{collection['title']}</b>\n"
        f"Ваш взнос: {util.money(collection['amount_per_person'], currency=True)}{due}"
        f"{code}"
    )
