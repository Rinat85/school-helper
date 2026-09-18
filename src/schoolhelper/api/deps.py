"""Аутентификация Mini App.

Клиент присылает initData в заголовке Authorization: tma <initData>.
Всё, чему мы верим из этой строки, — tg_user_id. Роли и class_id берутся из БД:
подделать запрос из браузера тривиально, и голосование по деньгам — первая мишень.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fastapi import Header, HTTPException

from ..core import roles as roles_mod
from ..core import security
from ..storage import klass as klass_repo
from ..storage import persons


@dataclass(frozen=True)
class Caller:
    person: sqlite3.Row
    roles: set[str]
    class_id: int

    @property
    def id(self) -> int:
        return int(self.person["id"])

    def require(self, permission: str) -> None:
        if not roles_mod.has(self.roles, permission):
            raise HTTPException(403, f"нет права: {permission}")

    def can(self, permission: str) -> bool:
        return roles_mod.has(self.roles, permission)


async def caller(authorization: str = Header(default="")) -> Caller:
    scheme, _, init_data = authorization.partition(" ")
    if scheme.lower() != "tma" or not init_data:
        raise HTTPException(401, "нужен заголовок Authorization: tma <initData>")

    try:
        tg_user_id = security.tg_user_id_from_init_data(init_data)
    except security.AuthError as exc:
        raise HTTPException(401, str(exc)) from exc

    class_id = klass_repo.default_id()
    person = persons.by_tg(class_id, tg_user_id)
    if person is None:
        raise HTTPException(403, "вас нет в списке класса")
    if person["status"] != "active":
        raise HTTPException(403, "профиль неактивен")

    return Caller(person=person, roles=roles_mod.roles_of(int(person["id"])), class_id=class_id)
