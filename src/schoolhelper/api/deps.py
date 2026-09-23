"""Аутентификация Mini App.

Клиент присылает initData в заголовке Authorization: tma <initData>.
Всё, чему мы верим из этой строки, — tg_user_id. Роли и class_id берутся из БД:
подделать запрос из браузера тривиально, и голосование по деньгам — первая мишень.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from fastapi import Header, HTTPException, Request

from .. import config
from ..core import logger, security
from ..core import roles as roles_mod
from ..storage import klass as klass_repo
from ..storage import persons

log = logger.get(__name__)

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_IN_DOCKER = Path("/.dockerenv").exists()


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


def _dev_user(request: Request) -> int | None:
    """Локальная разработка без Telegram: `Authorization: dev`.

    Работает только при заданном DEV_AUTH_TG_ID и только для запросов с localhost.
    В контейнере запросы приходят с адреса docker-сети, так что даже случайно
    оставленная переменная на сервере вход не откроет.
    """
    if not config.DEV_AUTH_TG_ID:
        return None
    if _IN_DOCKER:
        # Прод всегда в контейнере. Переменная, оставленная там по ошибке,
        # не должна открывать вход ни при какой маршрутизации адресов.
        log.warning("DEV_AUTH_TG_ID is set inside docker - dev auth ignored")
        return None
    host = request.client.host if request.client else ""
    if host not in _LOCAL_HOSTS:
        log.warning("dev auth attempt from non-local host %s rejected", host)
        return None
    return config.DEV_AUTH_TG_ID


async def caller(request: Request, authorization: str = Header(default="")) -> Caller:
    scheme, _, init_data = authorization.partition(" ")
    scheme = scheme.lower()

    if scheme == "dev":
        tg_user_id = _dev_user(request)
        if tg_user_id is None:
            raise HTTPException(401, "dev-вход выключен")
    elif scheme == "tma" and init_data:
        try:
            tg_user_id = security.tg_user_id_from_init_data(init_data)
        except security.AuthError as exc:
            raise HTTPException(401, str(exc)) from exc
    else:
        raise HTTPException(401, "нужен заголовок Authorization: tma <initData>")

    class_id = klass_repo.default_id()
    person = persons.by_tg(class_id, tg_user_id)
    if person is None:
        raise HTTPException(403, "вас нет в списке класса")
    if person["status"] != "active":
        raise HTTPException(403, "профиль неактивен")

    return Caller(person=person, roles=roles_mod.roles_of(int(person["id"])), class_id=class_id)


def bot(request: Request) -> Bot:
    """Бот, через которого API пишет в группу и в личку."""
    return request.app.state.bot
