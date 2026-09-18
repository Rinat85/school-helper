"""Логирование.

На Windows консоль по умолчанию cp1251 и падает на стрелках/эмодзи, поэтому
stdout принудительно переводится в UTF-8. Тем не менее в самих сообщениях лога
держим ASCII — логи читают через ssh, docker logs и Event Viewer.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .. import config

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
_configured = False


def setup() -> None:
    global _configured
    if _configured:
        return

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FORMAT))

    logfile = RotatingFileHandler(
        config.LOG_DIR / "school-helper.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    logfile.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    root.setLevel(config.LOG_LEVEL)
    root.handlers = [console, logfile]

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    _configured = True


def get(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(name)
