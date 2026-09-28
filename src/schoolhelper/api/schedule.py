"""Расписание в Mini App: родители смотрят, председатель проверяет и публикует."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core import util
from ..services import schedule as schedule_svc
from .deps import Caller, caller

router = APIRouter(prefix="/api/schedule")

EDIT = "timetable.edit"


@router.get("")
async def overview(user: Caller = Depends(caller)) -> dict:
    """Вчера и две недели вперёд; редактору — ещё черновики на проверку."""
    today = util.today()
    out: dict = {
        "today": str(today),
        "days": [
            schedule_svc.as_dict(row)
            for row in schedule_svc.upcoming(user.class_id, today - dt.timedelta(days=1), 15)
        ],
    }
    if user.can(EDIT):
        out["drafts"] = [
            schedule_svc.as_dict(row, with_source=True)
            for row in schedule_svc.drafts(user.class_id, today)
        ]
    return out


@router.get("/drafts/{draft_id}")
async def draft(draft_id: int, user: Caller = Depends(caller)) -> dict:
    user.require(EDIT)
    row = schedule_svc.get(draft_id)
    if row is None or int(row["class_id"]) != user.class_id:
        raise HTTPException(404, "черновик не найден")
    return schedule_svc.as_dict(row, with_source=True)


@router.get("/days/{date}")
async def day(date: dt.date, user: Caller = Depends(caller)) -> dict:
    row = schedule_svc.published(user.class_id, date)
    if row is None:
        raise HTTPException(404, "на этот день расписания нет")
    return schedule_svc.as_dict(row)


class DayIn(BaseModel):
    date: dt.date
    lessons: list[str] = Field(default_factory=list, max_length=schedule_svc.MAX_LESSONS)
    bring: list[str] = Field(default_factory=list, max_length=schedule_svc.MAX_BRING)
    note: str | None = Field(default=None, max_length=500)
    draft_id: int | None = None


@router.post("")
async def publish(body: DayIn, user: Caller = Depends(caller)) -> dict:
    user.require(EDIT)
    day_id = schedule_svc.publish(
        user.class_id,
        user.id,
        body.date,
        body.lessons,
        body.bring,
        body.note,
        draft_id=body.draft_id,
    )
    return schedule_svc.as_dict(schedule_svc.get(day_id))


@router.post("/drafts/{draft_id}/reject")
async def reject(draft_id: int, user: Caller = Depends(caller)) -> dict:
    user.require(EDIT)
    schedule_svc.reject(user.class_id, user.id, draft_id)
    return {"ok": True}


@router.delete("/days/{date}")
async def withdraw(date: dt.date, user: Caller = Depends(caller)) -> dict:
    user.require(EDIT)
    schedule_svc.withdraw(user.class_id, user.id, date)
    return {"ok": True}
