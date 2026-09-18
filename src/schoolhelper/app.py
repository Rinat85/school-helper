"""Один процесс: FastAPI отдаёт API и статику Mini App, aiogram живёт рядом.

Вебхук Telegram приходит в тот же FastAPI, статика — с того же домена.
Никакого CORS, один контейнер, Caddy спереди (SPEC §8).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api.routes import router as api_router
from .bot import notify
from .bot.factory import make_bot, make_dispatcher
from .core import logger, util
from .storage import db
from .storage import klass as klass_repo

log = logger.get(__name__)

bot = make_bot()
dispatcher = make_dispatcher()


STATE: dict = {"mode": "starting", "started_at": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.migrate()
    class_id = klass_repo.ensure_seeded()
    log.info("db ready, class_id=%s", class_id)

    tasks: list[asyncio.Task] = [asyncio.create_task(notify.pump_forever(bot))]

    # HTTP-сервер поднимается в обоих режимах: деплою нужен /health, даже когда
    # публичного домена ещё нет и бот работает опросом.
    if config.PUBLIC_URL and config.WEBHOOK_SECRET:
        url = f"{config.PUBLIC_URL}/tg/webhook/{config.WEBHOOK_SECRET}"
        await bot.set_webhook(url, drop_pending_updates=False)
        STATE["mode"] = "webhook"
        log.info("webhook set")
    else:
        await bot.delete_webhook(drop_pending_updates=False)
        # handle_signals=False: сигналами управляет uvicorn, иначе они конфликтуют.
        tasks.append(
            asyncio.create_task(dispatcher.start_polling(bot, handle_signals=False))
        )
        STATE["mode"] = "polling"
        log.info("polling started (no PUBLIC_URL/WEBHOOK_SECRET - webhook skipped)")

    STATE["started_at"] = util.now_iso()
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()


app = FastAPI(title="school-helper", lifespan=lifespan)
app.include_router(api_router)


@app.post("/tg/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request) -> JSONResponse:
    if not config.WEBHOOK_SECRET or secret != config.WEBHOOK_SECRET:
        raise HTTPException(404)
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dispatcher.feed_update(bot, update)
    return JSONResponse({"ok": True})


@app.get("/health")
async def health() -> dict:
    """То, что проверяет деплой перед тем, как признать выкатку удачной."""
    try:
        people = db.scalar("SELECT COUNT(*) FROM person")
        db_ok = True
    except Exception:  # noqa: BLE001
        people, db_ok = None, False

    return {
        "ok": db_ok and STATE["mode"] in ("polling", "webhook"),
        "mode": STATE["mode"],
        "started_at": STATE["started_at"],
        "revision": config.GIT_SHA or "dev",
        "db": {"ok": db_ok, "people": people},
    }


# Статика Mini App — последней, чтобы не перехватывать /api и /tg.
if (config.ROOT / "web").exists():
    app.mount("/", StaticFiles(directory=config.ROOT / "web", html=True), name="web")
