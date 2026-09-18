"""API Mini App: цепочка initData -> person -> роли из БД."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from helpers import make_init_data

from schoolhelper.app import app
from schoolhelper.core import roles as roles_mod
from schoolhelper.core import util
from schoolhelper.storage import persons


@pytest.fixture()
def client(fresh_db):
    return TestClient(app)


def auth(user_id: int) -> dict:
    return {"Authorization": "tma " + make_init_data(user_id)}


def test_health_is_open(client):
    assert client.get("/health").status_code == 200


def test_api_requires_init_data(client):
    assert client.get("/api/me").status_code == 401


def test_forged_init_data_is_rejected(client):
    assert client.get(
        "/api/me", headers={"Authorization": "tma user=%7B%22id%22%3A1%7D&hash=deadbeef"}
    ).status_code == 401


def test_stranger_is_not_let_in(client):
    """Подпись настоящая, но человека нет в классе."""
    assert client.get("/api/me", headers=auth(999_999)).status_code == 403


def test_member_sees_own_profile(client, fresh_db):
    person_id = persons.create(fresh_db, "Анна", tg_user_id=555, dm_open=True)
    body = client.get("/api/me", headers=auth(555)).json()

    assert body["person"]["id"] == person_id
    assert body["roles"] == ["parent"]
    assert body["can"]["collection.create"] is False
    assert body["sees_money"] is True


def test_roster_is_closed_to_parents(client, fresh_db):
    """Поимённый список «кто не сдал» — не для рядового родителя (SPEC §2)."""
    persons.create(fresh_db, "Анна", tg_user_id=556, dm_open=True)
    assert client.get("/api/collections/1/roster", headers=auth(556)).status_code == 403


def test_roster_is_open_to_treasurer(client, fresh_db):
    person_id = persons.create(fresh_db, "Мария", tg_user_id=557, dm_open=True)
    roles_mod.grant(person_id, roles_mod.TREASURER, by=None, now=util.now_iso())
    assert client.get("/api/collections/1/roster", headers=auth(557)).status_code == 200


def test_money_is_hidden_from_teacher(client, fresh_db):
    person_id = persons.create(fresh_db, "Гульнара А.", tg_user_id=558, dm_open=True)
    roles_mod.grant(person_id, roles_mod.TEACHER, by=None, now=util.now_iso())
    assert client.get("/api/money/summary", headers=auth(558)).status_code == 403


def test_webhook_secret_must_match(client):
    assert client.post("/tg/webhook/wrong-secret", json={}).status_code == 404
