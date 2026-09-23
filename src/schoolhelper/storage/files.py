"""Файлы храним в Telegram по file_id — бесплатно, без S3 (SPEC §5.6).

Ограничение: file_id живёт, пока жив бот. При смене токена ссылки теряются,
поэтому раз в год нужна выгрузка архива чеков.
"""

from __future__ import annotations

import sqlite3

from ..core import util
from . import db


def save_tg(
    class_id: int,
    tg_file_id: str,
    *,
    kind: str = "photo",
    tg_unique_id: str | None = None,
    mime: str | None = None,
    size: int | None = None,
    uploaded_by: int | None = None,
) -> int:
    return db.insert(
        "file",
        class_id=class_id,
        tg_file_id=tg_file_id,
        tg_unique_id=tg_unique_id,
        kind=kind,
        mime=mime,
        size=size,
        uploaded_by=uploaded_by,
        uploaded_at=util.now_iso(),
    )


def get(file_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM file WHERE id = ?", file_id)


def tg_id(file_id: int | None) -> str | None:
    if not file_id:
        return None
    row = db.one("SELECT tg_file_id FROM file WHERE id = ?", file_id)
    return row["tg_file_id"] if row else None
