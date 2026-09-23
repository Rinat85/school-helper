"""API комитета: люди, роли, приглашения, настройки класса."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core import security
from ..services import people as people_svc
from ..storage import klass as klass_repo
from ..storage import persons
from .deps import Caller, caller

router = APIRouter(prefix="/api")


# ── Люди ────────────────────────────────────────────────────────────────


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    child: str | None = Field(default=None, max_length=64)


class RoleIn(BaseModel):
    enabled: bool


@router.get("/people")
async def people(include_left: bool = False, user: Caller = Depends(caller)) -> list[dict]:
    user.require("person.manage")
    return people_svc.listing(user.class_id, include_left=include_left)


@router.post("/people")
async def add_person(body: PersonIn, user: Caller = Depends(caller)) -> dict:
    """Родитель без Telegram — взносы за него отмечает казначей."""
    user.require("person.manage")
    person_id = people_svc.add_offline(user.class_id, user.id, body.name, body.child)
    return people_svc.view(people_svc.get(user.class_id, person_id))


@router.patch("/people/{person_id}")
async def edit_person(person_id: int, body: PersonIn, user: Caller = Depends(caller)) -> dict:
    user.require("person.manage")
    people_svc.update(user.class_id, user.id, person_id, body.name, body.child)
    return people_svc.view(people_svc.get(user.class_id, person_id))


@router.post("/people/{person_id}/leave")
async def person_leaves(person_id: int, user: Caller = Depends(caller)) -> dict:
    user.require("person.manage")
    people_svc.mark_left(user.class_id, user.id, person_id)
    return {"ok": True}


@router.put("/people/{person_id}/roles/{role}")
async def set_role(
    person_id: int, role: str, body: RoleIn, user: Caller = Depends(caller)
) -> dict:
    user.require("role.grant")
    people_svc.set_role(user.class_id, user.id, person_id, role, body.enabled)
    return people_svc.view(people_svc.get(user.class_id, person_id))


@router.get("/invite")
async def invite(user: Caller = Depends(caller)) -> dict:
    """Ссылка-приглашение и сколько людей ещё не подключили бота."""
    user.require("person.manage")
    return {
        "link": security.deeplink("join", user.class_id),
        "not_connected": [
            {"id": int(row["id"]), "name": row["display_name"]}
            for row in persons.not_connected(user.class_id)
            if row["tg_user_id"] is not None
        ],
    }


# ── Настройки класса ────────────────────────────────────────────────────


class SettingsIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    school: str | None = Field(default=None, max_length=80)
    card_number: str | None = Field(default=None, max_length=32)
    card_holder: str | None = Field(default=None, max_length=64)


def _settings(class_id: int) -> dict:
    row = klass_repo.get(class_id)
    if row is None:
        raise HTTPException(404, "класс не найден")
    return {
        "name": row["name"],
        "school": row["school"],
        "card_number": row["card_number"],
        "card_holder": row["card_holder"],
        "currency": row["currency"],
        "group_bound": row["tg_chat_id"] is not None,
    }


@router.get("/settings")
async def get_settings(user: Caller = Depends(caller)) -> dict:
    if not (user.can("class.edit") or user.can("collection.create")):
        raise HTTPException(403, "нет права: class.edit")
    return _settings(user.class_id)


@router.put("/settings")
async def put_settings(body: SettingsIn, user: Caller = Depends(caller)) -> dict:
    """Название — председателю, реквизиты — тем, кто объявляет сборы."""
    current = _settings(user.class_id)

    if body.name is not None or body.school is not None:
        user.require("class.edit")
        klass_repo.set_info(
            user.class_id,
            (body.name or current["name"]).strip(),
            (body.school if body.school is not None else current["school"] or "").strip() or None,
        )

    if body.card_number is not None or body.card_holder is not None:
        user.require("collection.create")
        number = body.card_number if body.card_number is not None else current["card_number"]
        holder = body.card_holder if body.card_holder is not None else current["card_holder"]
        klass_repo.set_card(
            user.class_id,
            "".join((number or "").split()) or None,
            (holder or "").strip() or None,
        )

    return _settings(user.class_id)
