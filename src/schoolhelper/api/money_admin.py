"""API денег для комитета: сборы, очередь платежей, чеки."""

from __future__ import annotations

import datetime as dt
import sqlite3

from aiogram import Bot
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from ..bot import publisher
from ..core import logger, util
from ..core import roles as roles_mod
from ..services import collections as coll_svc
from ..services import payments as pay_svc
from ..services import people as people_svc
from ..storage import files, money
from .deps import Caller, bot, caller

log = logger.get(__name__)
router = APIRouter(prefix="/api")


def _own_collection(user: Caller, collection_id: int) -> sqlite3.Row:
    """Сбор этого класса. Чужой class_id — 404, а не 403: не подтверждаем, что он есть."""
    row = coll_svc.get(collection_id)
    if row is None or int(row["class_id"]) != user.class_id:
        raise HTTPException(404, "сбор не найден")
    return row


def _own_payment(user: Caller, payment_id: int) -> sqlite3.Row:
    collection = pay_svc.collection_of(payment_id)
    if collection is None or int(collection["class_id"]) != user.class_id:
        raise HTTPException(404, "платёж не найден")
    payment = pay_svc.get(payment_id)
    assert payment is not None
    return payment


def _collection_view(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "purpose": row["purpose"],
        "amount_per_person": int(row["amount_per_person"] or 0),
        "due_date": row["due_date"],
        "payment_code": row["payment_code"],
        "status": row["status"],
        "created_at": row["created_at"],
        "closed_at": row["closed_at"],
        **money.collection_progress(int(row["id"])),
    }


# ── Сборы ───────────────────────────────────────────────────────────────


class CollectionIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    amount_per_person: int = Field(gt=0, le=100_000_000)
    due_date: dt.date | None = None
    purpose: str | None = Field(default=None, max_length=500)


@router.get("/collections")
async def list_collections(user: Caller = Depends(caller)) -> list[dict]:
    if not roles_mod.sees_money(user.roles):
        raise HTTPException(403, "денежный раздел недоступен")
    return [_collection_view(row) for row in coll_svc.listing(user.class_id)]


@router.post("/collections")
async def create_collection(
    body: CollectionIn, user: Caller = Depends(caller), tg: Bot = Depends(bot)
) -> dict:
    """Создать, открыть и объявить — в группе и каждому лично."""
    user.require("collection.create")
    if body.due_date and body.due_date < util.today():  # дата класса, не сервера
        raise HTTPException(400, "срок уже прошёл")

    collection_id = coll_svc.create(
        user.class_id,
        user.id,
        title=body.title.strip(),
        amount_per_person=body.amount_per_person,
        due_date=body.due_date.isoformat() if body.due_date else None,
        purpose=(body.purpose or "").strip() or None,
    )
    coll_svc.open_(collection_id, user.id)
    result = await publisher.announce_collection(tg, collection_id)
    return {**_collection_view(_own_collection(user, collection_id)), **result}


@router.get("/collections/{collection_id}")
async def collection_detail(collection_id: int, user: Caller = Depends(caller)) -> dict:
    if not roles_mod.sees_money(user.roles):
        raise HTTPException(403, "денежный раздел недоступен")
    row = _own_collection(user, collection_id)

    result = _collection_view(row)
    mine = next(
        (r for r in money.my_contributions(user.id) if int(r["collection_id"]) == collection_id),
        None,
    )
    result["mine"] = (
        {
            "expected": int(mine["expected"]),
            "paid": int(mine["paid"]),
            "status": money.contribution_status(mine),
        }
        if mine
        else None
    )

    # Поимённый разрез — только money-ролям (SPEC §2). Остальным поля нет вовсе.
    if user.can("money.view_by_person"):
        result["roster"] = [
            {
                "person_id": int(r["person_id"]),
                "name": r["display_name"],
                "child": r["child_name"],
                "expected": int(r["expected"]),
                "paid": int(r["paid"]),
                "bot_connected": bool(r["dm_open"]),
                "status": money.contribution_status(r),
            }
            for r in money.collection_roster(collection_id)
        ]
    return result


@router.post("/collections/{collection_id}/close")
async def close_collection(
    collection_id: int, user: Caller = Depends(caller), tg: Bot = Depends(bot)
) -> dict:
    user.require("collection.create")
    row = _own_collection(user, collection_id)
    if row["status"] != "open":
        raise HTTPException(400, "сбор уже закрыт")
    coll_svc.close(collection_id, user.id)
    await publisher.refresh_progress(tg, _own_collection(user, collection_id))
    return _collection_view(_own_collection(user, collection_id))


class WaiveIn(BaseModel):
    waived: bool
    note: str | None = Field(default=None, max_length=200)


@router.put("/collections/{collection_id}/people/{person_id}/waive")
async def waive(
    collection_id: int,
    person_id: int,
    body: WaiveIn,
    user: Caller = Depends(caller),
    tg: Bot = Depends(bot),
) -> dict:
    """Освобождение от взноса — тихое, без статуса «должник» (SPEC §2)."""
    user.require("collection.create")
    row = _own_collection(user, collection_id)
    if coll_svc.contribution_of(collection_id, person_id) is None:
        raise HTTPException(404, "этого человека нет в сборе")
    coll_svc.set_waived(collection_id, person_id, user.id, body.waived, body.note)
    await publisher.refresh_progress(tg, row)  # цель сбора изменилась
    return _collection_view(_own_collection(user, collection_id))


class ManualPaymentIn(BaseModel):
    person_id: int
    amount: int | None = Field(default=None, gt=0)
    method: str = Field(default="cash", pattern="^(cash|transfer)$")


@router.post("/collections/{collection_id}/payments")
async def manual_payment(
    collection_id: int,
    body: ManualPaymentIn,
    user: Caller = Depends(caller),
    tg: Bot = Depends(bot),
    idempotency_key: str | None = Header(default=None),
) -> dict:
    """Казначей отмечает взнос сам: наличные или родитель без Telegram."""
    user.require("payment.confirm")
    row = _own_collection(user, collection_id)
    if row["status"] != "open":
        raise HTTPException(400, "сбор закрыт")
    people_svc.get(user.class_id, body.person_id)
    contribution = coll_svc.contribution_of(collection_id, body.person_id)
    if contribution is None:
        raise HTTPException(404, "этого человека нет в сборе")

    payment = pay_svc.record_manual(
        body.person_id,
        collection_id,
        amount=body.amount or int(contribution["expected"]),
        method=body.method,
        by_person_id=user.id,
        idempotency_key=idempotency_key,
    )
    await publisher.after_confirm(tg, payment)
    return _collection_view(_own_collection(user, collection_id))


# ── Очередь платежей ────────────────────────────────────────────────────


@router.get("/payments/pending")
async def pending_payments(user: Caller = Depends(caller)) -> list[dict]:
    user.require("payment.confirm")
    return [
        {
            "id": int(r["id"]),
            "person_id": int(r["person_id"]),
            "name": r["display_name"],
            "amount": int(r["amount"]),
            "method": r["method"],
            "collection_id": int(r["collection_id"]),
            "collection_title": r["collection_title"],
            "claimed_at": r["claimed_at"],
            "receipt_id": int(r["receipt_id"]) if r["receipt_id"] else None,
        }
        for r in pay_svc.pending(user.class_id)
    ]


@router.post("/payments/{payment_id}/confirm")
async def confirm_payment(
    payment_id: int, user: Caller = Depends(caller), tg: Bot = Depends(bot)
) -> dict:
    user.require("payment.confirm")
    _own_payment(user, payment_id)
    payment = pay_svc.confirm(payment_id, user.id)
    await publisher.after_confirm(tg, payment)
    return {"id": payment_id, "status": payment["status"]}


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


@router.post("/payments/{payment_id}/reject")
async def reject_payment(
    payment_id: int, body: RejectIn | None = None, user: Caller = Depends(caller)
) -> dict:
    user.require("payment.confirm")
    _own_payment(user, payment_id)
    reason = (body.reason if body else None) or "не найден в выписке"
    payment = pay_svc.reject(payment_id, user.id, reason=reason)
    publisher.after_reject(payment)
    return {"id": payment_id, "status": payment["status"]}


# ── Файлы ───────────────────────────────────────────────────────────────


@router.get("/files/{file_id}")
async def get_file(file_id: int, user: Caller = Depends(caller), tg: Bot = Depends(bot)):
    """Чек из Telegram. Браузер не может открыть file_id сам — отдаём через бота.

    Чек видит тот, кто его загрузил, и те, кто сверяет деньги. Больше никто.
    """
    row = files.get(file_id)
    if row is None or int(row["class_id"]) != user.class_id:
        raise HTTPException(404, "файл не найден")
    own = row["uploaded_by"] is not None and int(row["uploaded_by"]) == user.id
    if not (own or user.can("payment.confirm") or user.can("money.view_by_person")):
        raise HTTPException(403, "нет доступа к файлу")

    try:
        info = await tg.get_file(row["tg_file_id"])
        data = await tg.download_file(info.file_path)
    except Exception as exc:  # noqa: BLE001
        log.exception("cannot fetch file %s from telegram", file_id)
        raise HTTPException(502, "Telegram не отдал файл") from exc

    return Response(
        content=data.getvalue(),
        media_type=row["mime"] or "image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )
