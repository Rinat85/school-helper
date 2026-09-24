"""Один процесс: FastAPI отдаёт API и статику Mini App, aiogram живёт рядом.

Вебхук Telegram приходит в тот же FastAPI, статика — с того же домена.
Никакого CORS, один контейнер, Caddy спереди (SPEC §8).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from aiogram.types import MenuButtonWebApp, Update, WebAppInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api.admin import router as admin_router
from .api.money_admin import router as money_admin_router
from .api.routes import router as api_router
from .bot import menu, notify
from .bot.factory import make_bot, make_dispatcher
from .core import logger, util
from .services.payments import PaymentError
from .services.people import PeopleError
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
        # Явный список типов апдейтов: без my_chat_member бот не узнает,
        # что его добавили в чужую группу, и не выйдет из неё.
        await bot.set_webhook(
            url,
            drop_pending_updates=False,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
        STATE["mode"] = "webhook"
        log.info("webhook set")
    elif not config.BOT_POLLING:
        STATE["mode"] = "offline"
        log.warning("BOT_POLLING=0 - telegram updates are NOT received (local UI dev)")
    else:
        await bot.delete_webhook(drop_pending_updates=False)
        # handle_signals=False: сигналами управляет uvicorn, иначе они конфликтуют.
        tasks.append(
            asyncio.create_task(dispatcher.start_polling(bot, handle_signals=False))
        )
        STATE["mode"] = "polling"
        log.info("polling started (no PUBLIC_URL/WEBHOOK_SECRET - webhook skipped)")

    await _setup_menu_button()
    await menu.setup_commands(bot)

    STATE["started_at"] = util.now_iso()
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()


async def _setup_menu_button() -> None:
    """Кнопка меню бота открывает Mini App — главный вход для комитета.

    Telegram открывает Web App только по https, поэтому без домена кнопку
    не трогаем: пусть остаётся стандартный список команд.
    """
    if not config.PUBLIC_URL.startswith("https://"):
        return
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Класс", web_app=WebAppInfo(url=config.PUBLIC_URL))
        )
        log.info("menu button -> mini app")
    except Exception:  # noqa: BLE001 - бот работает и без кнопки
        log.exception("cannot set menu button")


app = FastAPI(title="school-helper", lifespan=lifespan)
app.state.bot = bot
app.include_router(api_router)
app.include_router(admin_router)
app.include_router(money_admin_router)


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """Кто что кеширует.

    index.html — всегда перепроверять (ETag делает это дёшево): в нём имена
    файлов текущей сборки, и старая копия после выкатки ведёт на файлы, которых
    уже нет. Файлы из /assets/ с хешем в имени не меняются никогда — их можно
    хранить сколько угодно. Ответы API — не кешировать вовсе: там деньги и люди.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(PeopleError)
@app.exception_handler(PaymentError)
async def business_rule_error(request: Request, exc: Exception) -> JSONResponse:
    """Нарушение правила — не сбой: текст ошибки показывается пользователю как есть."""
    return JSONResponse({"detail": str(exc)}, status_code=400)


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
        "ok": db_ok and STATE["mode"] in ("polling", "webhook", "offline"),
        "mode": STATE["mode"],
        "started_at": STATE["started_at"],
        "revision": config.GIT_SHA or "dev",
        "db": {"ok": db_ok, "people": people},
    }


# Статика Mini App — последней, чтобы не перехватывать /api и /tg.
if (config.ROOT / "web").exists():
    app.mount("/", StaticFiles(directory=config.ROOT / "web", html=True), name="web")
