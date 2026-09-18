"""Время, деньги, форматирование.

Время в БД — всегда UTC ISO-8601 ('2026-09-18T14:30:00Z').
Время на экране — всегда местное время класса (Asia/Tashkent).
Смешивать эти два представления нельзя нигде.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo  # на Windows требует пакет tzdata

from .. import config

UTC = dt.UTC
_MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_MONTHS_NOM = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def tz() -> ZoneInfo:
    return ZoneInfo(config.CLASS_TZ)


def now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def now_iso() -> str:
    """Метка времени для записи в БД."""
    return now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse(value: str) -> dt.datetime:
    """Читает то, что записал now_iso()."""
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def local(value: str | dt.datetime) -> dt.datetime:
    moment = parse(value) if isinstance(value, str) else value
    return moment.astimezone(tz())


def today() -> dt.date:
    """Сегодняшняя дата по часовому поясу класса, а не сервера."""
    return now().astimezone(tz()).date()


def date_ru(value: str | dt.date | dt.datetime, with_year: bool = False) -> str:
    """'18 сентября' / '18 сентября 2026'."""
    if isinstance(value, str):
        value = dt.date.fromisoformat(value) if len(value) == 10 else local(value).date()
    elif isinstance(value, dt.datetime):
        value = value.date()
    out = f"{value.day} {_MONTHS_GEN[value.month - 1]}"
    return f"{out} {value.year}" if with_year else out


def parse_date_ru(text: str) -> dt.date | None:
    """Принимает '15.12', '15.12.2026', '2026-12-15', '15 декабря'.

    Без года подставляется ближайший будущий: '15.12', введённое в январе,
    означает декабрь этого же года, а введённое в декабре — тоже этого,
    но если дата уже прошла — следующего.
    """
    raw = text.strip().lower().replace("/", ".").replace("-", ".")
    if not raw:
        return None

    parts = raw.split()
    if len(parts) >= 2 and not parts[1].isdigit():
        month_word = parts[1].rstrip(",.")
        if month_word in _MONTHS_GEN and parts[0].isdigit():
            month = _MONTHS_GEN.index(month_word) + 1
            year = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            return _complete(int(parts[0]), month, year)

    chunks = [c for c in raw.split(".") if c]
    if not all(c.isdigit() for c in chunks):
        return None
    try:
        if len(chunks) == 3 and len(chunks[0]) == 4:          # 2026.12.15
            return dt.date(int(chunks[0]), int(chunks[1]), int(chunks[2]))
        if len(chunks) == 3:                                   # 15.12.2026
            year = int(chunks[2])
            return _complete(int(chunks[0]), int(chunks[1]), year + 2000 if year < 100 else year)
        if len(chunks) == 2:                                   # 15.12
            return _complete(int(chunks[0]), int(chunks[1]), 0)
    except ValueError:
        return None
    return None


def _complete(day: int, month: int, year: int) -> dt.date | None:
    try:
        if year:
            return dt.date(year, month, day)
        base = today()
        candidate = dt.date(base.year, month, day)
        return candidate if candidate >= base else dt.date(base.year + 1, month, day)
    except ValueError:
        return None


def month_ru(value: dt.date) -> str:
    return f"{_MONTHS_NOM[value.month - 1]} {value.year}"


def weekday_ru(value: dt.date) -> str:
    return WEEKDAYS[value.weekday()]


# ── Деньги ──────────────────────────────────────────────────────────────
# Суммы всегда целые, в сумах. Никаких float — см. SPEC §4.4.


def money(amount: int | None, currency: bool = False) -> str:
    """1200000 -> '1 200 000' (неразрывные пробелы, чтобы не рвалось в Telegram)."""
    if amount is None:
        return "—"
    text = f"{int(amount):,}".replace(",", " ")
    return f"{text} сум" if currency else text


def plural(n: int, one: str, few: str, many: str) -> str:
    """'1 родитель' / '2 родителя' / '5 родителей'."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def progress_bar(current: int, target: int, width: int = 15) -> str:
    """'███████████░░░░' — прогресс сбора без имён и без «17 из 24»."""
    if target <= 0:
        return "░" * width
    filled = min(width, round(width * max(current, 0) / target))
    return "█" * filled + "░" * (width - filled)
