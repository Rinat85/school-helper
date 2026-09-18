"""Проверка подлинности: initData Mini App, deep-link'и, voter_hash.

Три правила из SPEC §9:
  1. initData проверяется на сервере, всегда;
  2. роль берётся ТОЛЬКО из БД — с клиента приходит лишь tg_user_id;
  3. class_id берётся из сессии, а не из параметров запроса.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl

from .. import config


class AuthError(Exception):
    """initData не прошла проверку."""


# ── Telegram Mini App initData ──────────────────────────────────────────


def parse_init_data(init_data: str, ttl: int | None = None) -> dict[str, Any]:
    """Проверяет подпись initData и возвращает разобранные поля.

    Бросает AuthError, если подпись не сходится или данные протухли.
    """
    if not init_data:
        raise AuthError("empty initData")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        raise AuthError("no hash in initData")

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise AuthError("bad initData signature")

    auth_date = int(pairs.get("auth_date", "0"))
    age = time.time() - auth_date
    if age > (config.INITDATA_TTL_SEC if ttl is None else ttl):
        raise AuthError("initData expired")

    if "user" in pairs:
        pairs["user"] = json.loads(pairs["user"])
    return pairs


def tg_user_id_from_init_data(init_data: str) -> int:
    user = parse_init_data(init_data).get("user") or {}
    uid = user.get("id")
    if not uid:
        raise AuthError("no user in initData")
    return int(uid)


# ── Подписанные deep-link'и (t.me/<bot>?start=<payload>) ────────────────
# Payload ограничен 64 символами и алфавитом A-Za-z0-9_-, поэтому подпись
# укорочена до 16 hex-символов (64 бита) — достаточно против подбора приглашения.


def _sign(payload: str, length: int = 16) -> str:
    return hmac.new(
        config.APP_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()[:length]


def make_deeplink_payload(kind: str, value: str | int) -> str:
    body = f"{kind}_{value}"
    return f"{body}_{_sign(body)}"


def verify_deeplink_payload(payload: str) -> tuple[str, str] | None:
    """('join', '1') либо None, если подпись не сходится."""
    parts = payload.rsplit("_", 1)
    if len(parts) != 2:
        return None
    body, sig = parts
    if not hmac.compare_digest(_sign(body), sig):
        return None
    kind, _, value = body.partition("_")
    if not kind or not value:
        return None
    return kind, value


def deeplink(kind: str, value: str | int) -> str:
    return f"https://t.me/{config.BOT_USERNAME}?start={make_deeplink_payload(kind, value)}"


# ── Анонимные голосования ───────────────────────────────────────────────


def voter_hash(poll_id: int, person_id: int) -> str:
    """Позволяет запретить двойной голос, не раскрывая, кто голосовал.

    Считается через APP_SECRET, а не BOT_TOKEN: утечка токена бота не должна
    давать возможность перебрать 24 человек и восстановить анонимное голосование.
    """
    return _sign(f"vote:{poll_id}:{person_id}", length=32)
