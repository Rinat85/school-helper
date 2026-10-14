"""Ежедневная зашифрованная копия базы."""

from __future__ import annotations

import datetime as dt
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient

from schoolhelper import config
from schoolhelper.app import app
from schoolhelper.bot import backup
from schoolhelper.core import util
from schoolhelper.storage import db, persons

KEY_HEX = "11" * 32
KEY = bytes.fromhex(KEY_HEX)
CHAIR_TG = 555


def _at(hour: int, day: dt.date | None = None) -> dt.datetime:
    day = day or util.today()
    return dt.datetime.combine(day, dt.time(hour, 0), tzinfo=util.tz())


def test_roundtrip_and_wrong_key():
    blob = backup.encrypt(b"SQLite format 3\x00 data", KEY)
    assert b"SQLite" not in blob
    assert backup.decrypt(blob, KEY) == b"SQLite format 3\x00 data"
    with pytest.raises(InvalidTag):
        backup.decrypt(blob, bytes(32))
    with pytest.raises(ValueError, match="не копия"):
        backup.decrypt(b"garbage", KEY)


def test_tampered_file_is_refused():
    blob = bytearray(backup.encrypt(b"data", KEY))
    blob[-1] ^= 1
    with pytest.raises(InvalidTag):
        backup.decrypt(bytes(blob), KEY)


@pytest.mark.parametrize("raw", ["", "abc", "11" * 16, "zz" * 32])
def test_bad_key_is_rejected(raw):
    with pytest.raises(backup.BackupKeyError):
        backup.key_from(raw)


async def test_daily_copy_is_a_real_database_sent_once(fresh_db, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BACKUP_TG_ID", 0)
    monkeypatch.setattr(config, "BOOTSTRAP_CHAIR_TG_ID", CHAIR_TG)
    persons.create(fresh_db, "Анна", tg_user_id=1001)
    bot = SimpleNamespace(send_document=AsyncMock())

    assert not backup.due(_at(2))  # до 03:00 рано
    assert backup.due(_at(4))
    await backup.send(bot, KEY, _at(4))
    assert not backup.due(_at(23))  # сегодня уже отправлено
    assert backup.due(_at(4, util.today() + dt.timedelta(days=1)))

    args, kwargs = bot.send_document.await_args
    assert args[0] == CHAIR_TG
    restored = tmp_path / "restored.db"
    restored.write_bytes(backup.decrypt(args[1].data, KEY))
    conn = sqlite3.connect(restored)
    assert conn.execute("SELECT display_name FROM person").fetchall() == [("Анна",)]
    conn.close()


def test_cli_decrypts_with_key_from_env(fresh_db, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BACKUP_KEY", KEY_HEX)
    source = tmp_path / "copy.db.enc"
    source.write_bytes(backup.encrypt(backup.snapshot(), KEY))
    target = tmp_path / "restored.db"

    assert backup._cli(["decrypt", str(source), str(target)]) == 0
    assert sqlite3.connect(target).execute("SELECT COUNT(*) FROM klass").fetchone()[0] == 1
    assert backup._cli(["decrypt", str(source), str(target)]) == 1  # не перезаписывает

    monkeypatch.setattr(config, "BACKUP_KEY", "22" * 32)
    assert backup._cli(["decrypt", str(source), str(tmp_path / "other.db")]) == 1


async def test_off_without_key(monkeypatch):
    monkeypatch.setattr(config, "BACKUP_KEY", "")
    bot = SimpleNamespace(send_document=AsyncMock())
    await backup.run_forever(bot)  # сразу возвращается, а не крутится
    bot.send_document.assert_not_awaited()


def test_webhook_rejects_wrong_secret(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "right-secret")
    client = TestClient(app)
    assert client.post("/tg/webhook/wrong-secret", json={}).status_code == 404


def test_db_snapshot_does_not_lock_live_db(fresh_db):
    backup.snapshot()
    db.set_meta("after_snapshot", "ok")  # база по-прежнему пишется
    assert db.get_meta("after_snapshot") == "ok"
