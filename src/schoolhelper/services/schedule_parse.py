"""Распознавание расписания на день из сообщения учительницы.

Сейчас — по правилам: нумерованный список уроков, строка «с собой / принести»,
слова «завтра», «на вторник», «29.09». Этого хватает для типичного сообщения:

    Расписание на завтра, 29.09:
    1. Математика
    2. Русский язык
    3. Физкультура
    С собой: краски, альбом

Что правила не поймут, председатель поправит в предпросмотре. Позже сюда
встанет распознавание моделью — с тем же ParsedDay на выходе, так что ни бот,
ни приложение этого не заметят.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
# Склонения, в которых день недели встречается в «на вторник», «в среду».
_WEEKDAYS = (
    ("понедельник",),
    ("вторник",),
    ("среда", "среду", "среды"),
    ("четверг",),
    ("пятница", "пятницу", "пятницы"),
    ("суббота", "субботу", "субботы"),
)

# Основы названий предметов начальной школы: по ним строка без номера
# распознаётся как урок. Нумерованным строкам словарь не нужен.
_SUBJECT_STEMS = (
    "матем",
    "русск",
    "родн",
    "чтени",
    "литер",
    "письм",
    "букв",
    "грамот",
    "англ",
    "узбек",
    "ona tili",
    "физ-ра",
    "физра",
    "физкульт",
    "музык",
    "изо",
    "рисов",
    "технол",
    "труд",
    "окруж",
    "природ",
    "информат",
    "этик",
    "логик",
    "шахмат",
    "классн",
    "воспит",
    "танц",
    "хорео",
    "ритм",
    "робот",
    "каллиграф",
    "развит",
)

_KEYCAP = re.compile("([0-9])️?⃣")  # «1️⃣» -> «1.»
_NUMBERED = re.compile(r"^\s*(\d{1,2})\s*(?:-?й\s+урок\s*[-–:.]?|урок\s*[-–:.]?|[.)\-:–])\s*(.+)$")
_TIME = re.compile(r"^\s*\d{1,2}:\d{2}(?:\s*[-–]\s*\d{1,2}:\d{2})?\s*[-–]?\s*")
_LEADING_DATE = re.compile(r"^\s*\d{1,2}\.\d{1,2}(?:\.\d{2,4})?(?!\d)")
_BULLET = re.compile(r"^\s*[-–—•*▪●◦✅✔🔹🔸]\s*(.+)$")
_BRING = re.compile(
    r"(принест\w*|принесите|взять|возьм\w*|с\s+собой|при\s+себе|не\s+забуд\w*|"
    r"нужн\w*\s+(?:будет\s+)?(?:иметь|принести|взять))",
    re.IGNORECASE,
)
_HEADER = re.compile(r"(расписани\w*|урок\w*)", re.IGNORECASE)
# Строка, которая начинается с дня: «Завтра:», «На среду», «Во вторник».
_DAY_START = re.compile(
    r"^\s*(?:на|во?)?\s*(?:завтра|послезавтра|сегодня|понедельник|вторник|сред[ау]|"
    r"четверг|пятниц[ау]|суббот[ау])\b",
    re.IGNORECASE,
)
_HEADER_COLON = re.compile(r"(?<!\d):(?!\d)")
# Предложения внутри строки: «…, физра. Принести линейку.» — но не «1. Математика».
_SENTENCE = re.compile(r"(?<=[^\d\s][.!?])\s+(?=[А-ЯЁA-Z])")
_GREETING = re.compile(
    r"^(добр\w+\s+(вечер|день|утро)\w*|здравствуйте|уважаемые\s+родители|"
    r"привет\w*|спасибо\w*)[\s!,.]*",
    re.IGNORECASE,
)
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")
_WORD_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\b", re.IGNORECASE)
_ITEM_SPLIT = re.compile(r"\s*[,;]\s*|\s+и\s+")


@dataclass
class ParsedDay:
    date: dt.date
    date_found: bool  # False — дату подставили сами: ближайший учебный день
    lessons: list[str] = field(default_factory=list)
    bring: list[str] = field(default_factory=list)
    note: str | None = None
    recognizer: str = "rules"


def next_school_day(today: dt.date) -> dt.date:
    """Завтра, а если завтра воскресенье — понедельник."""
    day = today + dt.timedelta(days=1)
    return day + dt.timedelta(days=1) if day.weekday() == 6 else day


def find_date(text: str, today: dt.date) -> dt.date | None:
    low = text.lower()

    for match in _NUMERIC_DATE.finditer(low):
        day, month, year = match.groups()
        found = _build_date(int(day), int(month), year, today)
        if found:
            return found
    match = _WORD_DATE.search(low)
    if match:
        found = _build_date(int(match.group(1)), _MONTHS.index(match.group(2)) + 1, None, today)
        if found:
            return found

    if "послезавтра" in low:
        return today + dt.timedelta(days=2)
    if "завтра" in low:
        return today + dt.timedelta(days=1)
    if "сегодня" in low:
        return today

    for weekday, forms in enumerate(_WEEKDAYS):
        if any(re.search(rf"\b{form}\b", low) for form in forms):
            ahead = (weekday - today.weekday()) % 7 or 7  # «на вторник» — ближайший впереди
            return today + dt.timedelta(days=ahead)
    return None


def _build_date(day: int, month: int, year: str | None, today: dt.date) -> dt.date | None:
    if year:
        full = int(year) + 2000 if len(year) == 2 else int(year)
        try:
            return dt.date(full, month, day)
        except ValueError:
            return None
    try:
        candidate = dt.date(today.year, month, day)
    except ValueError:
        return None
    # «05.01», написанное в декабре, — январь следующего года.
    if candidate < today - dt.timedelta(days=180):
        candidate = candidate.replace(year=today.year + 1)
    return candidate


def _items(text: str) -> list[str]:
    parts = (part.strip(" .!-–—:") for part in _ITEM_SPLIT.split(text))
    return [part for part in parts if part]


def _sentences(line: str) -> list[str]:
    # пустая строка остаётся: она разделяет блоки («С собой:» заканчивается на ней)
    return _SENTENCE.split(line) if line.strip() else [line]


def _looks_like_subject(line: str) -> bool:
    low = line.lower()
    return len(line) <= 40 and any(stem in low for stem in _SUBJECT_STEMS)


def parse(text: str, today: dt.date) -> ParsedDay | None:
    """None — в сообщении нет ни уроков, ни того, что взять с собой."""
    lessons: list[str] = []
    bring: list[str] = []
    notes: list[str] = []
    in_bring = False

    for raw in (part for text_line in text.splitlines() for part in _sentences(text_line)):
        line = _KEYCAP.sub(r"\1.", raw).strip()
        if not line:
            in_bring = False
            continue
        timed = _TIME.match(line)
        if timed and line[timed.end() :]:
            # «8:30 Математика» — время урока в списке не храним
            lessons.append(line[timed.end() :].strip().rstrip(".;,"))
            continue

        leading_date = _LEADING_DATE.match(line) is not None
        numbered = None if leading_date else _NUMBERED.match(line)
        bullet = _BULLET.match(line)
        if numbered or bullet:
            body = (numbered.group(2) if numbered else bullet.group(1)).strip()
            if in_bring:
                bring.extend(_items(body))
            elif bullet and not numbered and not _looks_like_subject(body):
                notes.append(body)
            else:
                lessons.append(body.rstrip(".;,"))
            continue

        bring_word = _BRING.search(line)
        if bring_word:
            rest = line[bring_word.end() :].lstrip(" :-—–")
            items = _items(rest)
            if items:
                bring.extend(items)
            else:
                in_bring = True  # «С собой:» — предметы на следующих строках
            continue

        if leading_date or _HEADER.search(line) or _DAY_START.match(line):
            # «Расписание на завтра: математика, чтение, англ»
            # двоеточие заголовка, а не времени: «собрание в 18:00, приходите» — не уроки
            parts = _HEADER_COLON.split(line, maxsplit=1)
            items = _items(parts[1]) if len(parts) == 2 else []
            if len(items) >= 2 and any(_looks_like_subject(item) for item in items):
                lessons.extend(items)
            continue

        if in_bring:
            bring.extend(_items(line))
            continue
        if _looks_like_subject(line):
            lessons.append(line.rstrip(".;,"))
            continue
        if _GREETING.match(line) and len(line) <= 60:
            continue  # «Добрый вечер, уважаемые родители!» — не примечание
        notes.append(line)

    if not lessons and not bring:
        return None

    # Дата — из заголовка («Расписание на 29.09»), а не из «сдать до 05.10» в тексте.
    headers = [line for line in text.splitlines() if _HEADER.search(line)]
    date = find_date("\n".join(headers), today) if headers else None
    date = date or find_date(text, today)
    note = "\n".join(notes)[:500] or None
    return ParsedDay(
        date=date or next_school_day(today),
        date_found=date is not None,
        lessons=lessons[:12],
        bring=bring[:20],
        note=note,
    )
