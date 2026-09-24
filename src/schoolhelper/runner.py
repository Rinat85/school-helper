"""Dev-режим: long polling, без вебхука и без публичного домена.

    python -m schoolhelper.runner

Прод — вебхук: uvicorn schoolhelper.app:app
"""

from __future__ import annotations

import asyncio

from . import config
from .bot import menu, notify
from .bot.factory import make_bot, make_dispatcher
from .core import logger
from .storage import db
from .storage import klass as klass_repo

log = logger.get(__name__)


async def main() -> None:
    problems = config.validate()
    if problems:
        for problem in problems:
            log.error("config: %s", problem)
        raise SystemExit(1)

    db.migrate()
    class_id = klass_repo.ensure_seeded()
    log.info("db ready, class_id=%s, db=%s", class_id, config.DB_PATH)

    bot = make_bot()
    dispatcher = make_dispatcher()

    me = await bot.get_me()
    log.info("bot @%s started (polling)", me.username)
    if not config.BOT_USERNAME:
        log.warning("BOT_USERNAME is empty - deep links will be broken; set it to %s", me.username)

    await bot.delete_webhook(drop_pending_updates=False)
    await menu.setup_commands(bot)
    pump = asyncio.create_task(notify.pump_forever(bot))
    try:
        await dispatcher.start_polling(bot)
    finally:
        pump.cancel()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("stopped")
