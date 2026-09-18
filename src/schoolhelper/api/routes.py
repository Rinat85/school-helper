"""API Mini App. Этап 1: профиль и касса — остальное появится на этапе 2."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..core import roles as roles_mod
from ..services import collections as coll_svc
from ..storage import klass as klass_repo
from ..storage import money, persons
from .deps import Caller, caller

router = APIRouter(prefix="/api")


@router.get("/me")
async def me(user: Caller = Depends(caller)) -> dict:
    klass_row = klass_repo.get(user.class_id)
    return {
        "person": {
            "id": user.id,
            "name": user.person["display_name"],
            "child": user.person["child_name"],
        },
        "roles": sorted(user.roles),
        "class": {
            "id": user.class_id,
            "name": klass_row["name"] if klass_row else None,
            "school": klass_row["school"] if klass_row else None,
            "currency": klass_row["currency"] if klass_row else None,
        },
        "can": {
            permission: user.can(permission)
            for permission in ("collection.create", "payment.confirm", "expense.approve",
                               "poll.create", "case.close", "audit.view")
        },
        "sees_money": roles_mod.sees_money(user.roles),
    }


@router.get("/money/summary")
async def money_summary(user: Caller = Depends(caller)) -> dict:
    if not roles_mod.sees_money(user.roles):
        raise HTTPException(403, "денежный раздел недоступен")

    return {
        **money.summary(user.class_id),
        "collections": [
            {
                "id": int(row["id"]),
                "title": row["title"],
                "due_date": row["due_date"],
                **money.collection_progress(int(row["id"])),
            }
            for row in coll_svc.open_ones(user.class_id)
        ],
    }


@router.get("/money/mine")
async def my_money(user: Caller = Depends(caller)) -> list[dict]:
    return [
        {
            "collection_id": int(row["collection_id"]),
            "title": row["title"],
            "expected": int(row["expected"]),
            "paid": int(row["paid"]),
            "due_date": row["due_date"],
            "status": money.contribution_status(row),
        }
        for row in money.my_contributions(user.id)
    ]


@router.get("/collections/{collection_id}/roster")
async def roster(collection_id: int, user: Caller = Depends(caller)) -> list[dict]:
    """Поимённый разрез сбора. Только money-роли — см. SPEC §2."""
    user.require("money.view_by_person")
    return [
        {
            "person_id": int(row["person_id"]),
            "name": row["display_name"],
            "expected": int(row["expected"]),
            "paid": int(row["paid"]),
            "dm_open": bool(row["dm_open"]),
            "status": money.contribution_status(row),
        }
        for row in money.collection_roster(collection_id)
    ]


@router.get("/people/not-connected")
async def not_connected(user: Caller = Depends(caller)) -> list[dict]:
    user.require("person.manage")
    return [
        {"id": int(row["id"]), "name": row["display_name"]}
        for row in persons.not_connected(user.class_id)
    ]
