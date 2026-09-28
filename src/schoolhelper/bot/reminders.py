"""Вечернее напоминание родителям: что завтра взять с собой.

С 19:00 до 22:00 по времени класса: раньше учительница может ещё не написать,
позже — ребёнок уже спит, а портфель собран. Опубликовали расписание в 20:30 —
напоминание уйдёт сразу. Каждый опубликованный день напоминается один раз.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from ..core import logger, util
from ..services import schedule as schedule_svc
from ..storage import db, persons
from . import notify

log = logger.get(__name__)

WINDOW = (dt.time(19, 0), dt.time(22, 0))


def tick(now: dt.datetime | None = None) -> int:
    """Ставит в очередь напоминания, которым пришло время. Возвращает их число."""
    local = (now or util.now()).astimezone(util.tz())
    if not (WINDOW[0] <= local.time() < WINDOW[1]):
        return 0

    tomorrow = local.date() + dt.timedelta(days=1)
    queued = 0
    for klass in db.query("SELECT id FROM klass"):
        class_id = int(klass["id"])
        day = schedule_svc.reminder_due(class_id, tomorrow)
        if day is None:
            continue
        queued += notify.enqueue_many(
            persons.parents(class_id),
            "schedule.tomorrow",
            "Напоминание на завтра 🌙\n\n" + schedule_svc.render(day),
            dedup_prefix=f"schedule:{day['id']}",
        )
        schedule_svc.mark_reminded(int(day["id"]))
        log.info("schedule %s: %s reminders queued", day["id"], queued)
    return queued


async def run_forever(interval: float = 60.0) -> None:
    log.info("schedule reminders started")
    while True:
        try:
            tick()
        except Exception:  # noqa: BLE001 - цикл не должен умирать от одной ошибки
            log.exception("schedule reminders error")
        await asyncio.sleep(interval)
