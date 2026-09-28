"""Расписание на день: от сообщения учительницы до напоминания родителям."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import MessageOriginUser, User
from fastapi.testclient import TestClient
from helpers import make_init_data

from schoolhelper.app import app
from schoolhelper.bot import reminders
from schoolhelper.bot import schedule as schedule_bot
from schoolhelper.core import roles as roles_mod
from schoolhelper.core import util
from schoolhelper.services import schedule as schedule_svc
from schoolhelper.storage import chats, db, persons
from schoolhelper.storage import klass as klass_repo

CHAIR_TG, PARENT_TG, TEACHER_TG = 1, 3, 77
TEACHER_CHAT = -300
CHAIR_ROLES = {roles_mod.PARENT, roles_mod.CHAIR}

TEXT = "Расписание на завтра:\n1. Математика\n2. Чтение\nС собой: краски"


@pytest.fixture()
def cls(fresh_db):
    class_id = fresh_db
    chair = persons.create(class_id, "Сергей", tg_user_id=CHAIR_TG, dm_open=True)
    roles_mod.grant(chair, roles_mod.CHAIR, by=None, now=util.now_iso())
    parent = persons.create(class_id, "Анна", tg_user_id=PARENT_TG, dm_open=True)
    chats.bind(class_id, TEACHER_CHAT, chats.TEACHER, "1 В и учитель", CHAIR_TG)
    klass_repo.set_teacher(class_id, TEACHER_TG, "Гульнара Рашидовна")
    return {"class_id": class_id, "chair": chair, "parent": parent}


def _bot():
    return SimpleNamespace(send_message=AsyncMock(), get_chat=AsyncMock())


def _group_message(from_id: int, text: str, message_id: int = 10, reply_to=None):
    return SimpleNamespace(
        chat=SimpleNamespace(id=TEACHER_CHAT, type="supergroup"),
        from_user=SimpleNamespace(id=from_id, is_bot=False, first_name="Гульнара", last_name=None,
                                  username=None),
        message_id=message_id,
        text=text,
        caption=None,
        reply_to_message=reply_to,
    )


def _sent_to(bot) -> list[int]:
    return [call.args[0] for call in bot.send_message.await_args_list]


def _drafts(class_id):
    return db.query("SELECT * FROM day_schedule WHERE class_id = ? AND status = 'draft'", class_id)


# ── Чат с учителем ──────────────────────────────────────────────────────


async def test_teacher_message_goes_to_chair_for_review(cls):
    bot = _bot()
    await schedule_bot.teacher_chat_message(_group_message(TEACHER_TG, TEXT), bot, cls["class_id"])

    assert _sent_to(bot) == [CHAIR_TG]  # и ни слова в чат с учителем
    draft = _drafts(cls["class_id"])[0]
    assert draft["date"] == str(util.today() + dt.timedelta(days=1))
    assert schedule_svc.as_dict(draft)["lessons"] == ["Математика", "Чтение"]
    # родители черновик не видят
    assert schedule_svc.published(cls["class_id"], draft["date"]) is None


async def test_same_message_twice_makes_one_draft(cls):
    bot = _bot()
    message = _group_message(TEACHER_TG, TEXT)
    await schedule_bot.teacher_chat_message(message, bot, cls["class_id"])
    await schedule_bot.teacher_chat_message(message, bot, cls["class_id"])
    assert len(_drafts(cls["class_id"])) == 1


async def test_other_people_in_teacher_chat_are_ignored(cls):
    bot = _bot()
    await schedule_bot.teacher_chat_message(_group_message(PARENT_TG, TEXT), bot, cls["class_id"])
    bot.send_message.assert_not_awaited()
    assert _drafts(cls["class_id"]) == []


async def test_teacher_announcement_is_not_a_draft(cls):
    bot = _bot()
    message = _group_message(TEACHER_TG, "Завтра собрание в 18:00, приходите")
    await schedule_bot.teacher_chat_message(message, bot, cls["class_id"])
    bot.send_message.assert_not_awaited()


async def test_chair_marks_teacher_by_reply(cls):
    klass_repo.set_teacher(cls["class_id"], None, None)
    bot = _bot()
    teacher_says = _group_message(TEACHER_TG, "Добрый день")
    command = _group_message(CHAIR_TG, "/учитель", reply_to=teacher_says)

    await schedule_bot.mark_teacher(command, bot, cls["class_id"], CHAIR_ROLES)

    assert klass_repo.is_teacher(cls["class_id"], TEACHER_TG)
    assert _sent_to(bot) == [CHAIR_TG]


async def test_parent_cannot_mark_teacher(cls):
    klass_repo.set_teacher(cls["class_id"], None, None)
    bot = _bot()
    command = _group_message(PARENT_TG, "/учитель", reply_to=_group_message(5, "Я учитель"))
    await schedule_bot.mark_teacher(command, bot, cls["class_id"], {roles_mod.PARENT})
    assert not klass_repo.is_teacher(cls["class_id"], 5)
    bot.send_message.assert_not_awaited()


async def test_force_parse_by_reply(cls):
    """Учительница не отмечена или написала необычно — председатель: /расписание."""
    bot = _bot()
    source = _group_message(12345, TEXT, message_id=55)
    command = _group_message(CHAIR_TG, "/расписание", message_id=56, reply_to=source)

    await schedule_bot.force_schedule(
        command, bot, cls["class_id"], persons.by_id(cls["chair"]), CHAIR_ROLES
    )

    assert _sent_to(bot) == [CHAIR_TG]
    assert schedule_svc.by_source(TEACHER_CHAT, 55) is not None


# ── Пересылка в личку ───────────────────────────────────────────────────


def _forward(text: str, author_id: int = TEACHER_TG):
    origin = MessageOriginUser(
        date=dt.datetime.now(dt.UTC),
        sender_user=User(id=author_id, is_bot=False, first_name="Гульнара"),
    )
    return SimpleNamespace(
        chat=SimpleNamespace(id=CHAIR_TG, type="private"),
        from_user=SimpleNamespace(id=CHAIR_TG),
        message_id=900,
        text=text,
        caption=None,
        forward_origin=origin,
    )


def _buttons(bot) -> list[str]:
    keyboard = bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard
    return [button.callback_data or "web_app" for row in keyboard for button in row]


async def test_forward_gives_preview_and_offers_to_remember_teacher(cls):
    klass_repo.set_teacher(cls["class_id"], None, None)
    bot = _bot()
    message = _forward(TEXT)
    assert schedule_bot._forward_from_editor(message, CHAIR_ROLES)

    await schedule_bot.forwarded(message, bot, cls["class_id"], persons.by_id(cls["chair"]))

    draft = _drafts(cls["class_id"])[0]
    assert draft["source"] == "forward"
    assert draft["source_user_id"] == TEACHER_TG
    assert _buttons(bot) == [
        f"sch:pub:{draft['id']}",
        f"sch:no:{draft['id']}",
        f"sch:teacher:{draft['id']}",
    ]


def test_parent_forward_is_not_taken(cls):
    """Пересланное родителем — не расписание: скриншот чека пусть идёт своим путём."""
    assert not schedule_bot._forward_from_editor(_forward(TEXT), {roles_mod.PARENT})


async def test_unrecognized_forward_has_no_publish_button(cls):
    bot = _bot()
    await schedule_bot.forwarded(
        _forward("Спасибо за праздник!"), bot, cls["class_id"], persons.by_id(cls["chair"])
    )
    assert not any(b.startswith("sch:pub") for b in _buttons(bot))


# ── Кнопки предпросмотра ────────────────────────────────────────────────


def _callback(data: str):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=CHAIR_TG),
        message=SimpleNamespace(edit_text=AsyncMock(), edit_reply_markup=AsyncMock()),
        answer=AsyncMock(),
    )


async def _draft(cls) -> int:
    await schedule_bot.teacher_chat_message(
        _group_message(TEACHER_TG, TEXT), _bot(), cls["class_id"]
    )
    return int(_drafts(cls["class_id"])[0]["id"])


async def test_publish_button(cls):
    draft_id = await _draft(cls)
    chair = persons.by_id(cls["chair"])

    await schedule_bot.preview_decision(
        _callback(f"sch:pub:{draft_id}"), _bot(), cls["class_id"], chair, CHAIR_ROLES
    )

    assert schedule_svc.get(draft_id)["status"] == "published"
    assert schedule_svc.get(draft_id)["confirmed_by"] == cls["chair"]


async def test_not_a_schedule_button(cls):
    draft_id = await _draft(cls)
    await schedule_bot.preview_decision(
        _callback(f"sch:no:{draft_id}"), _bot(), cls["class_id"],
        persons.by_id(cls["chair"]), CHAIR_ROLES,
    )
    assert schedule_svc.get(draft_id)["status"] == "rejected"


async def test_parent_cannot_publish(cls):
    draft_id = await _draft(cls)
    callback = _callback(f"sch:pub:{draft_id}")
    await schedule_bot.preview_decision(
        callback, _bot(), cls["class_id"], persons.by_id(cls["parent"]), {roles_mod.PARENT}
    )
    assert schedule_svc.get(draft_id)["status"] == "draft"


# ── Сервис ──────────────────────────────────────────────────────────────


def test_new_version_replaces_old_one(cls):
    tomorrow = util.today() + dt.timedelta(days=1)
    first = schedule_svc.publish(cls["class_id"], cls["chair"], tomorrow, ["Математика"], [], None)
    second = schedule_svc.publish(cls["class_id"], cls["chair"], tomorrow, ["Чтение"], [], None)

    assert schedule_svc.get(first)["status"] == "replaced"
    assert schedule_svc.published(cls["class_id"], tomorrow)["id"] == second


def test_empty_day_is_refused(cls):
    with pytest.raises(schedule_svc.ScheduleError):
        schedule_svc.publish(cls["class_id"], cls["chair"], util.today(), [" ", ""], [], "  ")


# ── API ─────────────────────────────────────────────────────────────────


def auth(user_id: int) -> dict:
    return {"Authorization": "tma " + make_init_data(user_id)}


async def test_api_review_and_publish(cls):
    draft_id = await _draft(cls)
    client = TestClient(app)

    overview = client.get("/api/schedule", headers=auth(CHAIR_TG)).json()
    assert [d["id"] for d in overview["drafts"]] == [draft_id]
    assert overview["drafts"][0]["source_text"] == TEXT
    assert "drafts" not in client.get("/api/schedule", headers=auth(PARENT_TG)).json()

    tomorrow = str(util.today() + dt.timedelta(days=1))
    body = {
        "date": tomorrow,
        "lessons": ["Математика", "Чтение", "Музыка"],
        "bring": ["краски"],
        "note": None,
        "draft_id": draft_id,
    }
    assert client.post("/api/schedule", json=body, headers=auth(PARENT_TG)).status_code == 403
    response = client.post("/api/schedule", json=body, headers=auth(CHAIR_TG))
    assert response.status_code == 200, response.text

    days = client.get("/api/schedule", headers=auth(PARENT_TG)).json()["days"]
    assert [(d["date"], d["lessons"]) for d in days] == [
        (tomorrow, ["Математика", "Чтение", "Музыка"])
    ]

    again = client.post("/api/schedule", json=body, headers=auth(CHAIR_TG))
    assert again.status_code == 400  # черновик уже опубликован

    assert client.delete(f"/api/schedule/days/{tomorrow}", headers=auth(CHAIR_TG)).json()["ok"]
    assert client.get("/api/schedule", headers=auth(PARENT_TG)).json()["days"] == []


def test_api_settings_show_chats_and_teacher(cls):
    client = TestClient(app)
    settings = client.get("/api/settings", headers=auth(CHAIR_TG)).json()
    assert settings["chats"][0]["role"] == "teacher"
    assert settings["teacher"] == {"name": "Гульнара Рашидовна"}
    assert settings["group_bound"] is False

    after = client.delete("/api/settings/teacher", headers=auth(CHAIR_TG)).json()
    assert after["teacher"] is None
    after = client.delete("/api/settings/chats/teacher", headers=auth(CHAIR_TG)).json()
    assert after["chats"] == []


# ── Вечернее напоминание ────────────────────────────────────────────────


def _at(hour: int, minute: int = 0) -> dt.datetime:
    local = dt.datetime.combine(util.today(), dt.time(hour, minute), tzinfo=util.tz())
    return local.astimezone(dt.UTC)


def _reminders():
    return db.query("SELECT * FROM notification WHERE kind = 'schedule.tomorrow'")


def test_evening_reminder_once_and_not_to_teacher(cls):
    teacher_person = persons.create(cls["class_id"], "Гульнара", tg_user_id=4, dm_open=True)
    roles_mod.grant(teacher_person, roles_mod.TEACHER, by=None, now=util.now_iso())
    tomorrow = util.today() + dt.timedelta(days=1)
    schedule_svc.publish(cls["class_id"], cls["chair"], tomorrow, ["Математика"], ["краски"], None)

    assert reminders.tick(_at(18, 30)) == 0  # рано
    assert reminders.tick(_at(19, 5)) == 2  # председатель и родитель
    assert reminders.tick(_at(19, 6)) == 0  # один раз

    recipients = {row["person_id"] for row in _reminders()}
    assert recipients == {cls["chair"], cls["parent"]}
    assert "краски" in _reminders()[0]["payload"]


def test_no_reminder_without_anything_to_bring(cls):
    tomorrow = util.today() + dt.timedelta(days=1)
    schedule_svc.publish(cls["class_id"], cls["chair"], tomorrow, ["Математика"], [], None)
    assert reminders.tick(_at(19, 30)) == 0


def test_no_reminder_late_at_night(cls):
    tomorrow = util.today() + dt.timedelta(days=1)
    schedule_svc.publish(cls["class_id"], cls["chair"], tomorrow, ["Математика"], ["форма"], None)
    assert reminders.tick(_at(22, 30)) == 0
