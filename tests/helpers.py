"""Общие вспомогалки для тестов."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from schoolhelper import config


def make_init_data(user_id: int = 42, auth_date: int | None = None) -> str:
    """Собирает подлинную initData так же, как это делает Telegram."""
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAA",
        "user": json.dumps({"id": user_id, "first_name": "Анна"}, ensure_ascii=False),
    }
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)
