"""Хроника событий (case_entry) и аудит-лог.

Обе ленты пишутся АВТОМАТИЧЕСКИ из кода, который меняет состояние.
Ручное ведение хроники умирает на второй месяц — см. SPEC §7.3.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..core import util
from . import db


def log_case(
    case_id: int | None,
    entry_type: str,
    *,
    text: str,
    actor_id: int | None = None,
    ref_type: str | None = None,
    ref_id: int | None = None,
    visibility: str = "all",
    at: str | None = None,
) -> int | None:
    """Строка в хронику События. Без case_id молча ничего не пишет:
    сбор или расход могут существовать вне События."""
    if case_id is None:
        return None
    return db.insert(
        "case_entry",
        case_id=case_id,
        at=at or util.now_iso(),
        type=entry_type,
        actor_id=actor_id,
        ref_type=ref_type,
        ref_id=ref_id,
        text=text,
        visibility=visibility,
    )


def case_timeline(case_id: int, *, money_access: bool) -> list[sqlite3.Row]:
    if money_access:
        return db.query("SELECT * FROM case_entry WHERE case_id = ? ORDER BY at, id", case_id)
    return db.query(
        "SELECT * FROM case_entry WHERE case_id = ? AND visibility = 'all' ORDER BY at, id",
        case_id,
    )


def audit(
    class_id: int,
    actor_id: int | None,
    action: str,
    *,
    object_type: str | None = None,
    object_id: int | None = None,
    before: Any = None,
    after: Any = None,
) -> int:
    """Каждая денежная мутация обязана оставлять здесь след (SPEC §4.4)."""
    return db.insert(
        "audit_log",
        class_id=class_id,
        at=util.now_iso(),
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        before=json.dumps(before, ensure_ascii=False) if before is not None else None,
        after=json.dumps(after, ensure_ascii=False) if after is not None else None,
    )
