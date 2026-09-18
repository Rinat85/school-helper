"""Очередь личных уведомлений.

Прямых send_message в личку в бизнес-коде быть не должно: Telegram отдаёт 429,
часть родителей блокирует бота, часть не нажала /start. Очередь даёт ретраи,
дедупликацию и честную картину «кому не дошло».
"""

from __future__ import annotations

import asyncio
import json

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..core import logger, util
from ..storage import db, persons

log = logger.get(__name__)

# Пауза между отправками: Telegram пропускает ~30 сообщений в секунду,
# на классе в 24 человека запас огромный, но лимит лучше не щупать.
_PACE_SEC = 0.06
_MAX_ATTEMPTS = 3


def enqueue(
    person_id: int,
    kind: str,
    text: str,
    *,
    buttons: list[list[dict]] | None = None,
    dedup_key: str | None = None,
    scheduled_at: str | None = None,
) -> int | None:
    """Ставит сообщение в очередь. dedup_key гасит повторы одного и того же повода."""
    if dedup_key and db.one("SELECT 1 FROM notification WHERE dedup_key = ?", dedup_key):
        return None
    return db.insert(
        "notification",
        person_id=person_id,
        kind=kind,
        payload=json.dumps({"text": text, "buttons": buttons or []}, ensure_ascii=False),
        dedup_key=dedup_key,
        scheduled_at=scheduled_at or util.now_iso(),
        created_at=util.now_iso(),
    )


def enqueue_many(
    people: list, kind: str, text: str, *, buttons: list[list[dict]] | None = None
) -> int:
    sent = 0
    for person in people:
        if not person["dm_open"]:
            continue
        if enqueue(int(person["id"]), kind, text, buttons=buttons):
            sent += 1
    return sent


def _markup(buttons: list[list[dict]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(**button) for button in row] for row in buttons
        ]
    )


async def pump_once(bot: Bot) -> int:
    """Отправляет всё, чему подошло время. Возвращает число отправленных."""
    rows = db.query(
        "SELECT n.*, p.tg_user_id FROM notification n "
        "JOIN person p ON p.id = n.person_id "
        "WHERE n.status = 'pending' AND n.scheduled_at <= ? "
        "ORDER BY n.scheduled_at LIMIT 100",
        util.now_iso(),
    )

    sent = 0
    for row in rows:
        if not row["tg_user_id"]:
            _mark(row["id"], "skipped", error="no tg_user_id")
            continue

        payload = json.loads(row["payload"])
        try:
            message = await bot.send_message(
                row["tg_user_id"],
                payload["text"],
                reply_markup=_markup(payload.get("buttons") or []),
            )
        except TelegramForbiddenError:
            # Заблокировал бота — личка закрыта, помечаем и больше не пытаемся.
            persons.mark_dm_closed(int(row["person_id"]))
            _mark(row["id"], "skipped", error="blocked by user")
        except TelegramRetryAfter as exc:
            log.warning("rate limited, retry after %ss", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
        except Exception as exc:  # noqa: BLE001 - очередь не должна падать целиком
            attempts = int(row["attempts"]) + 1
            status = "failed" if attempts >= _MAX_ATTEMPTS else "pending"
            db.execute(
                "UPDATE notification SET attempts = ?, status = ?, error = ? WHERE id = ?",
                attempts,
                status,
                str(exc)[:500],
                row["id"],
            )
            log.exception("notification %s failed", row["id"])
        else:
            db.execute(
                "UPDATE notification SET status = 'sent', sent_at = ?, tg_message_id = ? "
                "WHERE id = ?",
                util.now_iso(),
                message.message_id,
                row["id"],
            )
            sent += 1
            await asyncio.sleep(_PACE_SEC)

    return sent


def _mark(notification_id: int, status: str, error: str | None = None) -> None:
    db.execute(
        "UPDATE notification SET status = ?, error = ? WHERE id = ?",
        status,
        error,
        notification_id,
    )


async def pump_forever(bot: Bot, interval: float = 5.0) -> None:
    log.info("notification pump started")
    while True:
        try:
            await pump_once(bot)
        except Exception:  # noqa: BLE001
            log.exception("notification pump error")
        await asyncio.sleep(interval)
