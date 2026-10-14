"""Ежедневная зашифрованная копия базы — в личку председателю.

Снимки deploy.sh лежат на том же сервере: умрёт VPS — умрут и они. Эта копия
живёт в Telegram, вне сервера. В базе имена, имена детей, номер карты и журнал
денег, поэтому копия шифруется (AES-256-GCM) ключом BACKUP_KEY. Ключа нет —
копия не отправляется вовсе: открытым текстом базу не шлём никогда.

Ключ должен храниться и вне сервера (менеджер паролей): без него копия бесполезна.

Расшифровать:
    python -m schoolhelper.bot.backup decrypt school-2026-10-14.db.enc school.db
Ключ берётся из BACKUP_KEY (.env или окружение).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from aiogram import Bot
from aiogram.types import BufferedInputFile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .. import config
from ..core import logger, util
from ..storage import db

log = logger.get(__name__)

MAGIC = b"SHB1"  # формат файла: MAGIC | nonce (12 байт) | шифротекст gzip(базы)
SEND_AT = dt.time(3, 0)  # ночью: никто не пишет, база не меняется
META_KEY = "backup_sent_on"


class BackupKeyError(ValueError):
    """BACKUP_KEY не задан или не 64 hex-символа."""


def key_from(raw: str) -> bytes:
    try:
        key = bytes.fromhex(raw.strip())
    except ValueError:
        key = b""
    if len(key) != 32:
        raise BackupKeyError("BACKUP_KEY должен быть 64 hex-символа (32 байта)")
    return key


def encrypt(data: bytes, key: bytes) -> bytes:
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, gzip.compress(data), MAGIC)


def decrypt(blob: bytes, key: bytes) -> bytes:
    """Бросает ValueError на чужом файле, cryptography.InvalidTag — на чужом ключе."""
    if not blob.startswith(MAGIC):
        raise ValueError("это не копия school-helper")
    nonce, body = blob[len(MAGIC) : len(MAGIC) + 12], blob[len(MAGIC) + 12 :]
    return gzip.decompress(AESGCM(key).decrypt(nonce, body, MAGIC))


def snapshot() -> bytes:
    """Согласованный снимок живой базы (sqlite backup API, а не чтение файла при WAL)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "snapshot.db"
        target = sqlite3.connect(path)
        try:
            db.connect().backup(target)
        finally:
            target.close()
        return path.read_bytes()


def recipient() -> int:
    return config.BACKUP_TG_ID or config.BOOTSTRAP_CHAIR_TG_ID


def due(now: dt.datetime | None = None) -> bool:
    """После 03:00 по времени класса, если сегодня копию ещё не отправляли."""
    local = (now or util.now()).astimezone(util.tz())
    return local.time() >= SEND_AT and db.get_meta(META_KEY) != str(local.date())


async def send(bot: Bot, key: bytes, now: dt.datetime | None = None) -> None:
    local = (now or util.now()).astimezone(util.tz())
    raw = snapshot()
    blob = encrypt(raw, key)
    name = f"school-{local.date()}.db.enc"
    await bot.send_document(
        recipient(),
        BufferedInputFile(blob, filename=name),
        caption=(
            f"🔐 Копия базы за {util.date_ru(local.date(), with_year=True)}.\n"
            "Зашифрована ключом BACKUP_KEY. Расшифровать: "
            f"python -m schoolhelper.bot.backup decrypt {name} school.db"
        ),
        disable_notification=True,
    )
    db.set_meta(META_KEY, str(local.date()))
    log.info("backup sent to %s: %s bytes (db %s bytes)", recipient(), len(blob), len(raw))


async def run_forever(bot: Bot, interval: float = 600.0) -> None:
    if not config.BACKUP_KEY:
        log.warning("BACKUP_KEY is empty - daily backup to telegram is off")
        return
    try:
        key = key_from(config.BACKUP_KEY)
    except BackupKeyError:
        log.error("BACKUP_KEY is invalid - daily backup to telegram is off")
        return
    if not recipient():
        log.warning("no BACKUP_TG_ID / BOOTSTRAP_CHAIR_TG_ID - daily backup is off")
        return

    log.info("daily backup started, recipient %s", recipient())
    while True:
        try:
            if due():
                await send(bot, key)
        except Exception:  # noqa: BLE001 - не вышло — попробуем через interval
            log.exception("daily backup failed")
        await asyncio.sleep(interval)


def _cli(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "decrypt":
        print("использование: python -m schoolhelper.bot.backup decrypt <файл.enc> <куда.db>")
        return 2
    source, target = Path(argv[1]), Path(argv[2])
    if target.exists():
        print(f"{target} уже существует — не перезаписываю")
        return 1
    try:
        data = decrypt(source.read_bytes(), key_from(config.BACKUP_KEY))
    except BackupKeyError as exc:
        print(exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"не расшифровалось: {exc or 'неверный ключ или файл повреждён'}")
        return 1
    target.write_bytes(data)
    print(f"готово: {target} ({len(data)} байт)")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
