"""Подписи: initData, ссылки-приглашения, анонимные голоса."""

from __future__ import annotations

import time

import pytest
from helpers import make_init_data

from schoolhelper import config
from schoolhelper.core import security, util


def test_valid_init_data_passes():
    assert security.tg_user_id_from_init_data(make_init_data(777)) == 777


def test_tampered_init_data_is_rejected():
    """Подмена user_id в браузере — самый очевидный способ выдать себя за казначея."""
    raw = make_init_data(42).replace("42", "43")
    with pytest.raises(security.AuthError):
        security.parse_init_data(raw)


def test_stale_init_data_is_rejected():
    old = int(time.time()) - config.INITDATA_TTL_SEC - 60
    with pytest.raises(security.AuthError):
        security.parse_init_data(make_init_data(42, auth_date=old))


def test_empty_init_data_is_rejected():
    with pytest.raises(security.AuthError):
        security.parse_init_data("")


def test_deeplink_roundtrip():
    payload = security.make_deeplink_payload("join", 1)
    assert security.verify_deeplink_payload(payload) == ("join", "1")
    assert len(payload) <= 64  # ограничение Telegram на start-параметр


def test_forged_deeplink_is_rejected():
    assert security.verify_deeplink_payload("join_1_deadbeefdeadbeef") is None
    assert security.verify_deeplink_payload("join_1") is None
    assert security.verify_deeplink_payload("") is None


def test_voter_hash_is_stable_and_unique():
    a = security.voter_hash(5, 10)
    assert a == security.voter_hash(5, 10)          # повторный голос ловится
    assert a != security.voter_hash(5, 11)          # разные люди
    assert a != security.voter_hash(6, 10)          # разные голосования
    assert "10" not in a                            # person_id не восстановить глазами


def test_dates_are_parsed_the_way_people_write_them():
    assert util.parse_date_ru("15.12").month == 12
    assert util.parse_date_ru("15 декабря").day == 15
    assert util.parse_date_ru("2026-12-15").isoformat() == "2026-12-15"
    assert util.parse_date_ru("15.12.2026").isoformat() == "2026-12-15"
    assert util.parse_date_ru("абракадабра") is None


def test_money_formatting_uses_non_breaking_spaces():
    # В Telegram обычные пробелы рвут сумму по строкам.
    assert util.money(1_200_000) == "1 200 000"
    assert util.money(None) == "—"


def test_progress_bar_never_shows_names_or_counts():
    bar = util.progress_bar(850_000, 1_200_000)
    assert len(bar) == 15
    assert set(bar) <= {"█", "░"}
