"""Конфигурация из .env / переменных окружения."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _int(key: str, default: int) -> int:
    raw = _str(key)
    return int(raw) if raw else default


def _bool(key: str, default: bool) -> bool:
    raw = _str(key).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# ── Telegram ────────────────────────────────────────────────────────────
BOT_TOKEN = _str("BOT_TOKEN")
BOT_USERNAME = _str("BOT_USERNAME")          # без @, для deep-link
WEBHOOK_SECRET = _str("WEBHOOK_SECRET")      # путь вебхука: /tg/webhook/<secret>
PUBLIC_URL = _str("PUBLIC_URL").rstrip("/")  # https://class.example.com

# ── Приложение ──────────────────────────────────────────────────────────
DB_PATH = Path(_str("DB_PATH", str(ROOT / "data" / "school.db")))
LOG_DIR = Path(_str("LOG_DIR", str(ROOT / "data" / "logs")))
LOG_LEVEL = _str("LOG_LEVEL", "INFO")
HOST = _str("HOST", "0.0.0.0")
PORT = _int("PORT", 8080)

# Секрет для HMAC deep-link'ов и voter_hash. НЕ равен BOT_TOKEN:
# утечка токена не должна раскрывать анонимные голосования.
APP_SECRET = _str("APP_SECRET")

# ── Класс по умолчанию (сид при первом запуске) ─────────────────────────
CLASS_NAME = _str("CLASS_NAME", '1 «В»')
CLASS_SCHOOL = _str("CLASS_SCHOOL", "Школа №101")
CLASS_TZ = _str("CLASS_TZ", "Asia/Tashkent")
CLASS_CURRENCY = _str("CLASS_CURRENCY", "UZS")
# Латинский префикс для payment_code сборов: 1V-01, 1V-02 ...
CLASS_CODE = _str("CLASS_CODE", "1V")

# tg_user_id того, кто при первом /start получит роль chair + admin
BOOTSTRAP_CHAIR_TG_ID = _int("BOOTSTRAP_CHAIR_TG_ID", 0)

# ── Правила ─────────────────────────────────────────────────────────────
# Расходы до этой суммы утверждаются автоматически (см. SPEC §3.3).
EXPENSE_AUTO_APPROVE_UZS = _int("EXPENSE_AUTO_APPROVE_UZS", 100_000)

# Срок годности initData Mini App, секунды.
INITDATA_TTL_SEC = _int("INITDATA_TTL_SEC", 86_400)

# Антиспам напоминаний о взносе: за сколько дней до дедлайна + в день дедлайна.
REMIND_DAYS_BEFORE = _int("REMIND_DAYS_BEFORE", 3)


def validate() -> list[str]:
    """Возвращает список проблем конфигурации (пустой = всё в порядке)."""
    problems: list[str] = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN не задан")
    if not APP_SECRET:
        problems.append("APP_SECRET не задан (нужен для deep-link и анонимных голосований)")
    elif len(APP_SECRET) < 16:
        problems.append("APP_SECRET слишком короткий (нужно ≥16 символов)")
    return problems
