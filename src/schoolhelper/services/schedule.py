"""Расписание на день: черновик из сообщения учительницы -> проверка -> публикация.

Утверждённого расписания в классе пока нет: учительница пишет на завтра
текстом в своём чате. Бот распознаёт текст в черновик, председатель проверяет
и публикует. Родители видят только опубликованное — ошибку распознавания
в «с собой» лучше поймать до того, как ребёнок придёт без красок.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import sqlite3

from ..core import util
from ..storage import db, journal
from .schedule_parse import ParsedDay

MAX_LESSONS = 12
MAX_BRING = 20


class ScheduleError(ValueError):
    """Нарушение правила — текст показывается пользователю как есть."""


def get(day_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM day_schedule WHERE id = ?", day_id)


def by_source(chat_id: int, message_id: int) -> sqlite3.Row | None:
    return db.one(
        "SELECT * FROM day_schedule WHERE source_chat_id = ? AND source_message_id = ?",
        chat_id,
        message_id,
    )


def published(class_id: int, date: dt.date | str) -> sqlite3.Row | None:
    return db.one(
        "SELECT * FROM day_schedule WHERE class_id = ? AND date = ? AND status = 'published'",
        class_id,
        str(date),
    )


def upcoming(class_id: int, start: dt.date, days: int = 14) -> list[sqlite3.Row]:
    return db.query(
        "SELECT * FROM day_schedule WHERE class_id = ? AND status = 'published' "
        "AND date >= ? AND date < ? ORDER BY date",
        class_id,
        str(start),
        str(start + dt.timedelta(days=days)),
    )


def drafts(class_id: int, since: dt.date) -> list[sqlite3.Row]:
    """Черновики, которые ещё есть смысл проверять: на сегодня и позже."""
    return db.query(
        "SELECT * FROM day_schedule WHERE class_id = ? AND status = 'draft' AND date >= ? "
        "ORDER BY date, id DESC",
        class_id,
        str(since),
    )


def create_draft(
    class_id: int,
    parsed: ParsedDay | None,
    *,
    source: str,
    source_text: str,
    fallback_date: dt.date,
    source_chat_id: int | None = None,
    source_message_id: int | None = None,
    source_user_id: int | None = None,
    created_by: int | None = None,
) -> int:
    """Черновик из распознанного. parsed=None — распознать не вышло, но исходный
    текст сохраняется, чтобы заполнить день вручную рядом с ним."""
    return db.insert(
        "day_schedule",
        class_id=class_id,
        date=str(parsed.date if parsed else fallback_date),
        lessons=_dump(parsed.lessons if parsed else []),
        bring=_dump(parsed.bring if parsed else []),
        note=parsed.note if parsed else None,
        status="draft",
        source=source,
        source_text=source_text[:4000],
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        source_user_id=source_user_id,
        recognizer=parsed.recognizer if parsed else None,
        created_by=created_by,
        created_at=util.now_iso(),
    )


def reopen(day_id: int) -> None:
    """/расписание на сообщении, которое раньше отклонили: снова черновик."""
    db.execute(
        "UPDATE day_schedule SET status = 'draft' WHERE id = ? AND status = 'rejected'", day_id
    )


def publish(
    class_id: int,
    actor_id: int,
    date: dt.date,
    lessons: list[str],
    bring: list[str],
    note: str | None,
    draft_id: int | None = None,
) -> int:
    """Публикует день. Прежняя версия на эту дату уходит в историю ('replaced')."""
    lessons = _clean(lessons, MAX_LESSONS)
    bring = _clean(bring, MAX_BRING)
    note = (note or "").strip()[:500] or None
    if not lessons and not bring and not note:
        raise ScheduleError("пустой день: добавьте уроки, что взять с собой или примечание")
    if date < util.today() - dt.timedelta(days=1):
        raise ScheduleError("этот день уже прошёл")

    now = util.now_iso()
    with db.tx():
        if draft_id is not None:
            draft = get(draft_id)
            if draft is None or int(draft["class_id"]) != class_id:
                raise ScheduleError("черновик не найден")
            if draft["status"] != "draft":
                raise ScheduleError(_ALREADY[draft["status"]])
        db.execute(
            "UPDATE day_schedule SET status = 'replaced' "
            "WHERE class_id = ? AND date = ? AND status = 'published'",
            class_id,
            str(date),
        )
        values = {
            "date": str(date),
            "lessons": _dump(lessons),
            "bring": _dump(bring),
            "note": note,
            "status": "published",
            "confirmed_by": actor_id,
            "published_at": now,
        }
        if draft_id is not None:
            sets = ", ".join(f"{column} = ?" for column in values)
            db.execute(f"UPDATE day_schedule SET {sets} WHERE id = ?", *values.values(), draft_id)
            day_id = draft_id
        else:
            day_id = db.insert(
                "day_schedule",
                class_id=class_id,
                source="manual",
                created_by=actor_id,
                created_at=now,
                **values,
            )
        journal.audit(
            class_id,
            actor_id,
            "schedule.publish",
            object_type="day_schedule",
            object_id=day_id,
            after={"date": str(date), "lessons": lessons, "bring": bring},
        )
    return day_id


def reject(class_id: int, actor_id: int, draft_id: int) -> None:
    """«Это не расписание»: черновик больше не предлагается."""
    draft = get(draft_id)
    if draft is None or int(draft["class_id"]) != class_id:
        raise ScheduleError("черновик не найден")
    if draft["status"] != "draft":
        raise ScheduleError(_ALREADY[draft["status"]])
    db.execute("UPDATE day_schedule SET status = 'rejected' WHERE id = ?", draft_id)
    journal.audit(
        class_id, actor_id, "schedule.reject", object_type="day_schedule", object_id=draft_id
    )


def withdraw(class_id: int, actor_id: int, date: dt.date) -> None:
    """Снять опубликованный день — например, опубликовали не на ту дату."""
    row = published(class_id, date)
    if row is None:
        raise ScheduleError("на этот день ничего не опубликовано")
    db.execute("UPDATE day_schedule SET status = 'withdrawn' WHERE id = ?", row["id"])
    journal.audit(
        class_id, actor_id, "schedule.withdraw", object_type="day_schedule", object_id=row["id"]
    )


def reminder_due(class_id: int, tomorrow: dt.date) -> sqlite3.Row | None:
    """Опубликованный завтрашний день, о котором родителям ещё не напомнили.

    Напоминаем, только когда есть что взять с собой или примечание: список уроков
    каждый вечер в личку — это шум, его видно в приложении.
    """
    return db.one(
        "SELECT * FROM day_schedule WHERE class_id = ? AND date = ? AND status = 'published' "
        "AND reminded_at IS NULL AND (bring != '[]' OR note IS NOT NULL)",
        class_id,
        str(tomorrow),
    )


def mark_reminded(day_id: int) -> None:
    db.execute("UPDATE day_schedule SET reminded_at = ? WHERE id = ?", util.now_iso(), day_id)


# ── Представление ────────────────────────────────────────────────────────


def as_dict(row: sqlite3.Row, *, with_source: bool = False) -> dict:
    out = {
        "id": int(row["id"]),
        "date": row["date"],
        "lessons": json.loads(row["lessons"]),
        "bring": json.loads(row["bring"]),
        "note": row["note"],
        "status": row["status"],
        "source": row["source"],
        "published_at": row["published_at"],
    }
    if with_source:
        out["source_text"] = row["source_text"]
        out["recognizer"] = row["recognizer"]
    return out


def day_title(date: dt.date | str) -> str:
    """«Вторник, 29 сентября» — с «завтра»/«сегодня», когда это они."""
    day = dt.date.fromisoformat(date) if isinstance(date, str) else date
    name = util.WEEKDAYS[day.weekday()]
    delta = (day - util.today()).days
    prefix = {0: "Сегодня, ", 1: "Завтра, "}.get(delta, "")
    label = f"{name}, {util.date_ru(day)}"
    return prefix + label if prefix else label.capitalize()


def render(row: sqlite3.Row) -> str:
    """Текст дня для Telegram (HTML)."""
    lessons = json.loads(row["lessons"])
    bring = json.loads(row["bring"])
    lines = [f"📅 <b>{day_title(row['date'])}</b>"]
    if lessons:
        lines.append("")
        lines += [f"{number}. {html.escape(name)}" for number, name in enumerate(lessons, 1)]
    if bring:
        lines += ["", "🎒 <b>С собой:</b> " + html.escape(", ".join(bring))]
    if row["note"]:
        lines += ["", "📝 " + html.escape(row["note"])]
    return "\n".join(lines)


_ALREADY = {
    "published": "уже опубликовано",
    "replaced": "уже заменено более новой версией",
    "rejected": "уже отмечено как «не расписание»",
    "withdrawn": "уже снято с публикации",
}


def _clean(items: list[str], limit: int) -> list[str]:
    out = [" ".join(str(item).split())[:80] for item in items]
    return [item for item in out if item][:limit]


def _dump(items: list[str]) -> str:
    return json.dumps(items, ensure_ascii=False)
