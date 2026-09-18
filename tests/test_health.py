"""Старт приложения и /health — то, по чему деплой решает, удалась ли выкатка."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from schoolhelper import app as app_module


@pytest.fixture()
def stub_telegram(monkeypatch):
    """Подменяет сетевые вызовы Telegram — сам старт при этом настоящий."""
    monkeypatch.setattr(app_module.bot, "set_webhook", AsyncMock(return_value=True))
    monkeypatch.setattr(app_module.bot, "delete_webhook", AsyncMock(return_value=True))
    monkeypatch.setattr(app_module.dispatcher, "start_polling", AsyncMock(return_value=None))
    monkeypatch.setattr(app_module.bot.session, "close", AsyncMock(return_value=None))


def test_polling_mode_without_domain(fresh_db, stub_telegram, monkeypatch):
    """Без PUBLIC_URL бот работает опросом, но HTTP-сервер всё равно поднят."""
    monkeypatch.setattr(app_module.config, "PUBLIC_URL", "")
    monkeypatch.setattr(app_module.config, "WEBHOOK_SECRET", "")

    with TestClient(app_module.app) as client:
        body = client.get("/health").json()

    assert body["ok"] is True
    assert body["mode"] == "polling"
    assert body["db"]["ok"] is True
    app_module.dispatcher.start_polling.assert_awaited()
    app_module.bot.set_webhook.assert_not_awaited()


def test_webhook_mode_with_domain(fresh_db, stub_telegram, monkeypatch):
    monkeypatch.setattr(app_module.config, "PUBLIC_URL", "https://class.example.com")
    monkeypatch.setattr(app_module.config, "WEBHOOK_SECRET", "s3cret")

    with TestClient(app_module.app) as client:
        body = client.get("/health").json()

    assert body["mode"] == "webhook"
    app_module.bot.set_webhook.assert_awaited_once_with(
        "https://class.example.com/tg/webhook/s3cret", drop_pending_updates=False
    )
    app_module.dispatcher.start_polling.assert_not_awaited()


def test_health_reports_revision(fresh_db, stub_telegram, monkeypatch):
    """По /health видно, какой коммит крутится — это проверяет deploy.sh."""
    monkeypatch.setattr(app_module.config, "PUBLIC_URL", "")
    monkeypatch.setattr(app_module.config, "GIT_SHA", "abc1234")

    with TestClient(app_module.app) as client:
        assert client.get("/health").json()["revision"] == "abc1234"
