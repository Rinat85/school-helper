"""Постоянное расписание уроков и кружков."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from helpers import make_init_data

from schoolhelper.app import app
from schoolhelper.core import roles as roles_mod
from schoolhelper.core import util
from schoolhelper.services import timetable as timetable_svc
from schoolhelper.services.schedule import ScheduleError
from schoolhelper.storage import db, persons

CHAIR_TG, PARENT_TG = 1, 3

BODY = {
    "lessons": {
        "1": ["Час будущего", "Чтение", "Математика", "Физкультура", "Письмо"],
        "2": ["Чтение", "Письмо", "Математика", "Информатика"],
    },
    "clubs": [
        {"weekday": 1, "title": "Английский язык", "starts_at": "15:00", "ends_at": "16:00"},
        {"weekday": 5, "title": "Футбол и танцы", "starts_at": "14:00", "ends_at": "15:00"},
    ],
}


@pytest.fixture()
def cls(fresh_db):
    class_id = fresh_db
    chair = persons.create(class_id, "Сергей", tg_user_id=CHAIR_TG, dm_open=True)
    roles_mod.grant(chair, roles_mod.CHAIR, by=None, now=util.now_iso())
    persons.create(class_id, "Анна", tg_user_id=PARENT_TG, dm_open=True)
    return {"class_id": class_id, "chair": chair}


def auth(user_id: int) -> dict:
    return {"Authorization": "tma " + make_init_data(user_id)}


def test_no_timetable_yet(cls):
    client = TestClient(app)
    assert client.get("/api/schedule", headers=auth(PARENT_TG)).json()["timetable"] is None


def test_chair_saves_parents_see(cls):
    client = TestClient(app)
    denied = client.put("/api/schedule/timetable", json=BODY, headers=auth(PARENT_TG))
    assert denied.status_code == 403

    response = client.put("/api/schedule/timetable", json=BODY, headers=auth(CHAIR_TG))
    assert response.status_code == 200, response.text

    week = client.get("/api/schedule", headers=auth(PARENT_TG)).json()["timetable"]
    assert [d["weekday"] for d in week["days"]] == [1, 2, 5]
    monday, tuesday, friday = week["days"]
    assert monday["lessons"] == BODY["lessons"]["1"]
    assert monday["clubs"] == [
        {"title": "Английский язык", "starts_at": "15:00", "ends_at": "16:00"}
    ]
    assert tuesday["clubs"] == []
    assert friday == {
        "weekday": 5,
        "lessons": [],
        "clubs": [{"title": "Футбол и танцы", "starts_at": "14:00", "ends_at": "15:00"}],
    }


def test_new_version_replaces_old_but_keeps_history(cls):
    class_id, chair = cls["class_id"], cls["chair"]
    first = timetable_svc.save(class_id, chair, {1: ["Чтение"]}, [])
    second = timetable_svc.save(class_id, chair, {1: ["Математика"]}, [])

    today = util.today()
    assert timetable_svc.week(class_id, today)["id"] == second
    assert timetable_svc.week(class_id, today)["days"][0]["lessons"] == ["Математика"]
    old = db.one("SELECT * FROM timetable_version WHERE id = ?", first)
    assert old["valid_to"] == str(today)
    assert db.scalar("SELECT COUNT(*) FROM lesson_slot WHERE version_id = ?", first) == 1
    assert timetable_svc.week(class_id, today - dt.timedelta(days=1)) is None


@pytest.mark.parametrize(
    ("lessons", "club", "error"),
    [
        ({}, None, "пустое"),
        ({1: ["  ", ""]}, None, "пустое"),
        ({8: ["Чтение"]}, None, "день недели"),
        ({}, timetable_svc.Club(1, "Рисование", "14:00", "13:00"), "раньше"),
        ({}, timetable_svc.Club(1, "Рисование", "9:00", "10:00"), "15:00"),
        ({}, timetable_svc.Club(1, "  ", "13:00", "14:00"), "названия"),
    ],
)
def test_bad_timetable_is_refused(cls, lessons, club, error):
    with pytest.raises(ScheduleError, match=error):
        timetable_svc.save(cls["class_id"], cls["chair"], lessons, [club] if club else [])
    assert db.scalar("SELECT COUNT(*) FROM timetable_version") == 0
