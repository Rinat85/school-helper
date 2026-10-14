"""Расходы в Mini App: казначей вносит, ревизор утверждает, родители видят отчёт."""

from __future__ import annotations

import base64
import binascii
import datetime as dt

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..bot import expenses as expense_bot
from ..core import logger
from ..core import roles as roles_mod
from ..services import expenses as expense_svc
from ..storage import money, persons
from .deps import Caller, bot, caller

log = logger.get(__name__)
router = APIRouter(prefix="/api/expenses")

# Mini App ужимает фото до ~1600px JPEG — это десятки-сотни КБ. 4 МБ — с запасом.
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG")


def _money_section(user: Caller) -> None:
    if not roles_mod.sees_money(user.roles):
        raise HTTPException(403, "денежный раздел недоступен")


@router.get("")
async def overview(user: Caller = Depends(caller)) -> dict:
    """Утверждённые — всем, кому виден денежный раздел; ждущие и отклонённые — комитету."""
    _money_section(user)
    committee = bool(user.roles & roles_mod.MONEY_ROLES)
    return {
        "balance": money.balance(user.class_id),
        "items": [
            expense_svc.as_dict(row)
            for row in expense_svc.listing(user.class_id, with_unapproved=committee)
        ],
        "has_auditor": bool(persons.with_role(user.class_id, roles_mod.AUDITOR)),
    }


class ExpenseIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    amount: int = Field(gt=0, le=expense_svc.MAX_AMOUNT)
    spent_at: dt.date
    note: str | None = Field(default=None, max_length=500)
    # фото чека, base64 без префикса data:
    receipt: str | None = Field(default=None, max_length=MAX_RECEIPT_BYTES * 4 // 3 + 4)


@router.post("")
async def create(body: ExpenseIn, user: Caller = Depends(caller), tg: Bot = Depends(bot)) -> dict:
    user.require("expense.create")
    # Поля проверяем до загрузки чека, чтобы не плодить в Telegram чеки без расходов.
    expense_svc.validate(body.title, body.amount, body.spent_at)

    receipt_id = None
    if body.receipt:
        data = _decode_image(body.receipt)
        try:
            receipt_id = await expense_bot.upload_receipt(
                tg, user.class_id, user.person, data, body.title
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("cannot upload expense receipt for person %s", user.id)
            raise HTTPException(
                502, "не удалось сохранить чек: откройте бота и нажмите /start"
            ) from exc

    shortfall = expense_svc.shortfall(user.class_id, body.amount)
    expense_id = expense_svc.create(
        user.class_id,
        user.id,
        title=body.title,
        amount=body.amount,
        spent_at=body.spent_at,
        note=body.note,
        receipt_id=receipt_id,
    )
    notified = await expense_bot.notify_auditors(tg, expense_id, shortfall)
    return {
        **expense_svc.as_dict(expense_svc.get(expense_id)),
        "shortfall": shortfall,
        "auditors_notified": notified,
    }


@router.post("/{expense_id}/approve")
async def approve(expense_id: int, user: Caller = Depends(caller)) -> dict:
    user.require("expense.approve")
    expense = expense_svc.approve(user.class_id, expense_id, user.id)
    expense_bot.after_decision(expense)
    return expense_svc.as_dict(expense)


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


@router.post("/{expense_id}/reject")
async def reject(
    expense_id: int, body: RejectIn | None = None, user: Caller = Depends(caller)
) -> dict:
    user.require("expense.approve")
    reason = body.reason if body else None
    expense = expense_svc.reject(user.class_id, expense_id, user.id, reason)
    expense_bot.after_decision(expense, reason)
    return expense_svc.as_dict(expense)


def _decode_image(encoded: str) -> bytes:
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "чек не читается — выберите фото ещё раз") from exc
    if len(data) > MAX_RECEIPT_BYTES:
        raise HTTPException(400, "фото чека слишком большое")
    if not data.startswith(_IMAGE_MAGIC):
        raise HTTPException(400, "чек должен быть фотографией (JPEG или PNG)")
    return data
