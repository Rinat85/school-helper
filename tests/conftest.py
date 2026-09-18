"""Общая подготовка тестов.

config читает окружение на импорте, поэтому переменные выставляются до того,
как будет импортирован любой модуль пакета.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="school-helper-test-"))
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-FOR-UNIT-TESTS")
os.environ.setdefault("APP_SECRET", "test-secret-for-unit-tests-0123456789")
os.environ.setdefault("BOT_USERNAME", "test_class_bot")
os.environ["DB_PATH"] = str(_TMP / "test.db")
os.environ["LOG_DIR"] = str(_TMP / "logs")

import pytest  # noqa: E402

from schoolhelper.storage import db, persons  # noqa: E402
from schoolhelper.storage import klass as klass_repo


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Чистая БД на каждый тест."""
    from schoolhelper import config

    path = tmp_path / "school.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(db._local, "conn", None, raising=False)

    db.migrate()
    class_id = klass_repo.ensure_seeded()
    yield class_id

    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
    db._local.conn = None


@pytest.fixture()
def parents(fresh_db):
    """Три родителя: обычный, казначей, председатель."""
    from schoolhelper.core import roles as roles_mod
    from schoolhelper.core import util

    class_id = fresh_db
    anna = persons.create(class_id, "Анна", tg_user_id=1001, dm_open=True)
    maria = persons.create(class_id, "Мария", tg_user_id=1002, dm_open=True)
    sergey = persons.create(class_id, "Сергей", tg_user_id=1003, dm_open=True)

    now = util.now_iso()
    roles_mod.grant(maria, roles_mod.TREASURER, by=None, now=now)
    roles_mod.grant(sergey, roles_mod.CHAIR, by=None, now=now)
    return {"class_id": class_id, "anna": anna, "maria": maria, "sergey": sergey}
