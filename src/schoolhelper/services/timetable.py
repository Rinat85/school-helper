"""Постоянное расписание: уроки и кружки по дням недели.

Это фон. Что на самом деле будет завтра, решает учительница: опубликованное
расписание на день (services/schedule.py) всегда главнее недельного.

Расписание не правится на месте — сохранение создаёт новую версию, которая
действует с сегодняшнего дня. Прошлые дни остаются при той версии, что была.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass

from ..core import util
from ..storage import db, journal
from .schedule import MAX_LESSONS, ScheduleError

MAX_CLUBS = 20

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class Club:
    weekday: int  # 1=Пн
    title: str
    starts_at: str  # '15:00'
    ends_at: str


def current(class_id: int, date: dt.date) -> sqlite3.Row | None:
    """Версия, действующая в этот день. При двух сохранениях за день — последняя."""
    return db.one(
        "SELECT * FROM timetable_version WHERE class_id = ? AND published_at IS NOT NULL "
        "AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?) "
        "ORDER BY valid_from DESC, id DESC LIMIT 1",
        class_id,
        str(date),
        str(date),
    )


def week(class_id: int, date: dt.date) -> dict | None:
    """Неделя по версии, действующей в этот день; None — расписания ещё нет.

    days — только дни, где что-то есть: {weekday, lessons, clubs}, weekday 1=Пн.
    """
    version = current(class_id, date)
    if version is None:
        return None
    days: dict[int, dict] = {}

    def day(weekday: int) -> dict:
        return days.setdefault(weekday, {"weekday": weekday, "lessons": [], "clubs": []})

    for row in db.query(
        "SELECT weekday, subject FROM lesson_slot WHERE version_id = ? ORDER BY weekday, slot_no",
        version["id"],
    ):
        day(int(row["weekday"]))["lessons"].append(row["subject"])
    for row in db.query(
        "SELECT weekday, title, starts_at, ends_at FROM club_slot WHERE version_id = ? "
        "ORDER BY weekday, starts_at, id",
        version["id"],
    ):
        day(int(row["weekday"]))["clubs"].append(
            {"title": row["title"], "starts_at": row["starts_at"], "ends_at": row["ends_at"]}
        )
    return {
        "id": int(version["id"]),
        "valid_from": version["valid_from"],
        "days": [days[weekday] for weekday in sorted(days)],
    }


def save(
    class_id: int,
    actor_id: int,
    lessons: dict[int, list[str]],
    clubs: list[Club],
) -> int:
    """Новая версия с сегодняшнего дня; действующая закрывается этим же днём."""
    lessons = {
        weekday: _clean(names, MAX_LESSONS) for weekday, names in lessons.items() if names
    }
    if any(not 1 <= weekday <= 7 for weekday in lessons):
        raise ScheduleError("день недели — от 1 (понедельник) до 7")
    clubs = [_club(club) for club in clubs]
    if len(clubs) > MAX_CLUBS:
        raise ScheduleError(f"кружков больше {MAX_CLUBS} — так не бывает")
    if not any(lessons.values()) and not clubs:
        raise ScheduleError("пустое расписание: добавьте уроки хотя бы на один день")

    today = str(util.today())
    now = util.now_iso()
    with db.tx():
        db.execute(
            "UPDATE timetable_version SET valid_to = ? WHERE class_id = ? AND valid_to IS NULL",
            today,
            class_id,
        )
        version_id = db.insert(
            "timetable_version",
            class_id=class_id,
            valid_from=today,
            published_at=now,
            created_by=actor_id,
            created_at=now,
        )
        for weekday, names in lessons.items():
            for slot_no, subject in enumerate(names, 1):
                db.insert(
                    "lesson_slot",
                    version_id=version_id,
                    weekday=weekday,
                    slot_no=slot_no,
                    subject=subject,
                )
        for club in clubs:
            db.insert(
                "club_slot",
                version_id=version_id,
                weekday=club.weekday,
                title=club.title,
                starts_at=club.starts_at,
                ends_at=club.ends_at,
            )
        journal.audit(
            class_id,
            actor_id,
            "timetable.save",
            object_type="timetable_version",
            object_id=version_id,
            after={
                "lessons": {str(weekday): names for weekday, names in lessons.items()},
                "clubs": [club.__dict__ for club in clubs],
            },
        )
    return version_id


def _club(club: Club) -> Club:
    title = " ".join(club.title.split())[:80]
    if not title:
        raise ScheduleError("у кружка нет названия")
    if not 1 <= club.weekday <= 7:
        raise ScheduleError(f"«{title}»: день недели — от 1 (понедельник) до 7")
    if not _TIME.match(club.starts_at) or not _TIME.match(club.ends_at):
        raise ScheduleError(f"«{title}»: время пишется как 15:00")
    if club.starts_at >= club.ends_at:
        raise ScheduleError(f"«{title}»: кружок заканчивается раньше, чем начинается")
    return Club(club.weekday, title, club.starts_at, club.ends_at)


def _clean(items: list[str], limit: int) -> list[str]:
    out = [" ".join(str(item).split())[:80] for item in items]
    return [item for item in out if item][:limit]
