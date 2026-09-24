"""Пришедший по ссылке ждёт одобрения председателя.

Ссылку-приглашение пересылают. Без одобрения в класс вошёл бы любой, кому она
попала, — и получил бы реквизиты карты, сводку кассы и строчку в каждом сборе.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from helpers import make_init_data

from schoolhelper import config
from schoolhelper.app import app
from schoolhelper.core import roles as roles_mod
from schoolhelper.core import util
from schoolhelper.services import collections as coll_svc
from schoolhelper.services import people as people_svc
from schoolhelper.storage import db, persons


def auth(tg_id: int) -> dict:
    return {"Authorization": "tma " + make_init_data(tg_id)}


@pytest.fixture()
def cls(fresh_db):
    class_id = fresh_db
    chair = persons.create(class_id, "Сергей", tg_user_id=1, dm_open=True)
    roles_mod.grant(chair, roles_mod.CHAIR, by=None, now=util.now_iso())
    parent = persons.create(class_id, "Анна", tg_user_id=3, dm_open=True)
    return {"class_id": class_id, "chair": chair, "parent": parent}


def _join(class_id: int, tg_id: int = 700, name: str = "Незнакомец"):
    """Как это делает бот, когда человек приходит по ссылке."""
    return persons.upsert_from_tg(class_id, tg_id, display_name=name)


# ── Статус «ждёт» ───────────────────────────────────────────────────────


def test_newcomer_by_link_is_pending(cls):
    row = _join(cls["class_id"])
    assert persons.is_pending(row)
    assert not persons.is_member(row)


def test_pending_is_not_counted_in_collections(cls):
    _join(cls["class_id"])
    collection_id = coll_svc.create(
        cls["class_id"], cls["chair"], title="Цветы", amount_per_person=10_000
    )
    people = {r["person_id"] for r in db.query(
        "SELECT person_id FROM contribution WHERE collection_id = ?", collection_id
    )}
    assert people == {cls["chair"], cls["parent"]}


def test_pending_cannot_open_mini_app(cls):
    _join(cls["class_id"], tg_id=700)
    response = TestClient(app).get("/api/me", headers=auth(700))
    assert response.status_code == 403
    assert "одобрения" in response.json()["detail"]


def test_bootstrap_chair_needs_no_approval(cls, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_CHAIR_TG_ID", 900)
    row = _join(cls["class_id"], tg_id=900)
    assert persons.is_member(row)
    assert roles_mod.CHAIR in roles_mod.roles_of(int(row["id"]))


def test_no_roles_before_approval(cls):
    row = _join(cls["class_id"])
    with pytest.raises(people_svc.PeopleError, match="одобрите"):
        people_svc.set_role(cls["class_id"], cls["chair"], int(row["id"]), "treasurer", True)


# ── Решение председателя ────────────────────────────────────────────────


def test_approve_makes_member_and_enrolls_in_open_collections(cls):
    collection_id = coll_svc.create(
        cls["class_id"], cls["chair"], title="Цветы", amount_per_person=10_000
    )
    coll_svc.open_(collection_id, cls["chair"])
    row = _join(cls["class_id"])

    enrolled = people_svc.approve(cls["class_id"], cls["chair"], int(row["id"]))

    assert persons.is_member(persons.by_id(int(row["id"])))
    assert [c["title"] for c in enrolled] == ["Цветы"]
    contribution = coll_svc.contribution_of(collection_id, int(row["id"]))
    assert contribution["expected"] == 10_000


def test_decision_is_made_once(cls):
    """Два председателя нажали одновременно — второй получает понятный отказ."""
    row = _join(cls["class_id"])
    people_svc.approve(cls["class_id"], cls["chair"], int(row["id"]))
    with pytest.raises(people_svc.PeopleError, match="уже рассмотрена"):
        people_svc.decline(cls["class_id"], cls["chair"], int(row["id"]))


def test_declined_can_apply_again(cls):
    row = _join(cls["class_id"], tg_id=700)
    people_svc.decline(cls["class_id"], cls["chair"], int(row["id"]))
    assert persons.by_id(int(row["id"]))["status"] == "left"

    again = _join(cls["class_id"], tg_id=700)
    assert again["id"] == row["id"]
    assert persons.is_pending(again)


def test_removed_member_returns_through_approval(cls):
    """Исключённый по старой ссылке не возвращается сам — только через председателя."""
    persons.mark_left(cls["parent"])
    again = _join(cls["class_id"], tg_id=3)
    assert persons.is_pending(again)


# ── API ─────────────────────────────────────────────────────────────────


def test_api_approve_and_decline(cls):
    client = TestClient(app)
    first = _join(cls["class_id"], tg_id=701, name="Ольга")
    second = _join(cls["class_id"], tg_id=702, name="Пётр")

    people = client.get("/api/people", headers=auth(1)).json()
    pending = {p["name"] for p in people if not p["approved"]}
    assert pending == {"Ольга", "Пётр"}
    assert client.get("/api/home", headers=auth(1)).json()["pending_people"] == 2

    assert client.post(f"/api/people/{first['id']}/approve", headers=auth(1)).json()["approved"]
    assert client.post(f"/api/people/{second['id']}/decline", headers=auth(1)).status_code == 200
    assert client.get("/api/home", headers=auth(1)).json()["pending_people"] == 0

    kinds = {r["kind"] for r in db.query(
        "SELECT kind FROM notification WHERE person_id = ?", first["id"]
    )}
    assert "person.approved" in kinds


def test_parent_cannot_approve(cls):
    row = _join(cls["class_id"])
    response = TestClient(app).post(f"/api/people/{row['id']}/approve", headers=auth(3))
    assert response.status_code == 403
