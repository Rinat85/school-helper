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
    for name in ("set_chat_menu_button", "set_my_commands", "delete_my_commands"):
        monkeypatch.setattr(app_module.bot, name, AsyncMock(return_value=True))


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
    call = app_module.bot.set_webhook.await_args
    assert call.args == ("https://class.example.com/tg/webhook/s3cret",)
    # без my_chat_member бот не узнает, что его добавили в чужую группу
    assert "my_chat_member" in call.kwargs["allowed_updates"]
    app_module.dispatcher.start_polling.assert_not_awaited()


def test_health_reports_revision(fresh_db, stub_telegram, monkeypatch):
    """По /health видно, какой коммит крутится — это проверяет deploy.sh."""
    monkeypatch.setattr(app_module.config, "PUBLIC_URL", "")
    monkeypatch.setattr(app_module.config, "GIT_SHA", "abc1234")

    with TestClient(app_module.app) as client:
        assert client.get("/health").json()["revision"] == "abc1234"


def test_menu_button_opens_mini_app_with_https_domain(fresh_db, stub_telegram, monkeypatch):
    monkeypatch.setattr(app_module.config, "PUBLIC_URL", "https://class.example.com")
    monkeypatch.setattr(app_module.config, "WEBHOOK_SECRET", "s3cret")

    with TestClient(app_module.app):
        pass

    button = app_module.bot.set_chat_menu_button.await_args.kwargs["menu_button"]
    assert button.web_app.url == "https://class.example.com"


def test_no_menu_button_without_domain(fresh_db, stub_telegram, monkeypatch):
    """Telegram открывает Mini App только по https — без домена кнопку не трогаем."""
    monkeypatch.setattr(app_module.config, "PUBLIC_URL", "")

    with TestClient(app_module.app):
        pass

    app_module.bot.set_chat_menu_button.assert_not_awaited()
