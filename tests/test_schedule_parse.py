"""Распознавание расписания из текста учительницы — по правилам, без ИИ."""

from __future__ import annotations

import datetime as dt

from schoolhelper.services.schedule_parse import find_date, next_school_day, parse

MONDAY = dt.date(2026, 9, 28)


def test_typical_message():
    text = (
        "Добрый вечер, уважаемые родители!\n"
        "Расписание на завтра, 29.09 (вторник):\n"
        "1. Математика\n"
        "2. Русский язык\n"
        "3. Физкультура (форма!)\n"
        "4. Музыка\n"
        "С собой: альбом, краски и кисти\n"
        "Сбор на экскурсию до 05.10"
    )
    day = parse(text, MONDAY)
    assert day.date == dt.date(2026, 9, 29)
    assert day.date_found
    assert day.lessons == ["Математика", "Русский язык", "Физкультура (форма!)", "Музыка"]
    assert day.bring == ["альбом", "краски", "кисти"]
    # приветствие не примечание, а дата «до 05.10» не сбивает дату дня
    assert day.note == "Сбор на экскурсию до 05.10"


def test_one_line_message():
    day = parse("Завтра: математика, чтение, англ, физра. Принести линейку.", MONDAY)
    assert day.date == dt.date(2026, 9, 29)
    assert day.lessons == ["математика", "чтение", "англ", "физра"]
    assert day.bring == ["линейку"]


def test_keycap_numbers_and_bring_block():
    text = "На среду\n1️⃣ Чтение\n2️⃣ Окружающий мир\n3️⃣ ИЗО\nНе забудьте:\n- цветную бумагу\n- клей"
    day = parse(text, MONDAY)
    assert day.date == dt.date(2026, 9, 30)
    assert day.lessons == ["Чтение", "Окружающий мир", "ИЗО"]
    assert day.bring == ["цветную бумагу", "клей"]


def test_lesson_times_are_dropped():
    day = parse("8:30 Математика\n9:25 Письмо\n10:20 Узбекский язык", MONDAY)
    assert day.lessons == ["Математика", "Письмо", "Узбекский язык"]
    assert not day.date_found
    assert day.date == dt.date(2026, 9, 29)


def test_announcement_is_not_a_schedule():
    assert parse("Родительское собрание в пятницу в 18:00", MONDAY) is None
    assert parse("Спасибо всем!", MONDAY) is None


def test_date_words():
    assert find_date("на послезавтра", MONDAY) == dt.date(2026, 9, 30)
    assert find_date("в пятницу", MONDAY) == dt.date(2026, 10, 2)
    assert find_date("на понедельник", MONDAY) == dt.date(2026, 10, 5)  # следующий, не сегодня
    assert find_date("на 1 октября", MONDAY) == dt.date(2026, 10, 1)
    assert find_date("на 05.01", dt.date(2026, 12, 20)) == dt.date(2027, 1, 5)


def test_saturday_is_followed_by_monday():
    saturday = dt.date(2026, 10, 3)
    assert next_school_day(saturday) == dt.date(2026, 10, 5)
