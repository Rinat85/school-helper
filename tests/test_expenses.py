"""Расходы: казначей вносит, ревизор утверждает, касса меняется только после этого."""

from __future__ import annotations

import base64
import datetime as dt
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from helpers import make_init_data

from schoolhelper.app import app
from schoolhelper.bot import expenses as expense_bot
from schoolhelper.core import roles as roles_mod
from schoolhelper.core import util
from schoolhelper.services import expenses as expense_svc
from schoolhelper.storage import db, money, persons

PARENT_TG, TREASURER_TG, CHAIR_TG, AUDITOR_TG, TEACHER_TG = 1, 2, 3, 4, 5
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


@pytest.fixture()
def cls(fresh_db):
    class_id = fresh_db
    now = util.now_iso()
    people = {}
    for name, tg, role in [
        ("Анна", PARENT_TG, None),
        ("Мария", TREASURER_TG, roles_mod.TREASURER),
        ("Сергей", CHAIR_TG, roles_mod.CHAIR),
        ("Ольга", AUDITOR_TG, roles_mod.AUDITOR),
    ]:
        people[name] = persons.create(class_id, name, tg_user_id=tg, dm_open=True)
        if role:
            roles_mod.grant(people[name], role, by=None, now=now)
    teacher = persons.create(class_id, "Учитель", tg_user_id=TEACHER_TG, dm_open=True)
    roles_mod.grant(teacher, roles_mod.TEACHER, by=None, now=now)
    roles_mod.revoke(teacher, roles_mod.PARENT, by=None, now=now)
    money.add_entry(class_id, "in", 500_000, "adjustment", memo="остаток")
    return {"class_id": class_id, **people}


@pytest.fixture()
def tg(monkeypatch):
    """Бот без сети: send_photo возвращает сообщение с file_id, как Telegram."""
    sent = SimpleNamespace(
        photo=[SimpleNamespace(file_id="F-small", file_unique_id="u1", file_size=10),
               SimpleNamespace(file_id="F-big", file_unique_id="u2", file_size=100)]
    )
    bot = app.state.bot
    monkeypatch.setattr(bot, "send_photo", AsyncMock(return_value=sent))
    monkeypatch.setattr(bot, "send_message", AsyncMock())
    return bot


def auth(user_id: int) -> dict:
    return {"Authorization": "tma " + make_init_data(user_id)}


def _new(cls, amount=120_000, by="Мария") -> int:
    return expense_svc.create(
        cls["class_id"], cls[by], title="Подарок учителю", amount=amount, spent_at=util.today()
    )


# ── Правило двух рук ────────────────────────────────────────────────────


def test_money_leaves_only_after_auditor_approves(cls):
    expense_id = _new(cls)
    assert money.balance(cls["class_id"]) == 500_000  # заявлен — ещё не потрачен

    expense = expense_svc.approve(cls["class_id"], expense_id, cls["Ольга"])
    assert expense["status"] == "approved"
    assert money.balance(cls["class_id"]) == 380_000
    entry = db.one("SELECT * FROM ledger_entry WHERE source_type = 'expense'")
    assert (entry["direction"], entry["amount"], entry["source_id"]) == ("out", 120_000, expense_id)

    with pytest.raises(expense_svc.ExpenseError, match="уже утверждён"):
        expense_svc.approve(cls["class_id"], expense_id, cls["Ольга"])
    assert money.balance(cls["class_id"]) == 380_000  # не списано дважды


def test_small_expense_still_needs_approval(cls):
    """Порога «мелочь без подписи» нет."""
    _new(cls, amount=1_000)
    assert money.balance(cls["class_id"]) == 500_000


def test_own_expense_cannot_be_approved(cls):
    roles_mod.grant(cls["Мария"], roles_mod.AUDITOR, by=None, now=util.now_iso())
    expense_id = _new(cls, by="Мария")
    with pytest.raises(expense_svc.ExpenseError, match="свой"):
        expense_svc.approve(cls["class_id"], expense_id, cls["Мария"])
    with pytest.raises(expense_svc.ExpenseError, match="свой"):
        expense_svc.reject(cls["class_id"], expense_id, cls["Мария"], None)


def test_only_auditor_approves():
    assert roles_mod.has({roles_mod.AUDITOR}, "expense.approve")
    for role in (roles_mod.CHAIR, roles_mod.TREASURER, roles_mod.PARENT, roles_mod.ADMIN):
        assert not roles_mod.has({role}, "expense.approve")


def test_rejected_never_reaches_the_ledger(cls):
    expense_id = _new(cls)
    expense = expense_svc.reject(cls["class_id"], expense_id, cls["Ольга"], "нет чека")
    assert expense["status"] == "rejected"
    assert "Отклонено: нет чека" in expense["note"]
    with pytest.raises(expense_svc.ExpenseError, match="отклонён"):
        expense_svc.approve(cls["class_id"], expense_id, cls["Ольга"])
    assert money.balance(cls["class_id"]) == 500_000


def test_overdraft_is_allowed_with_warning(cls):
    assert expense_svc.shortfall(cls["class_id"], 400_000) == 0
    _new(cls, amount=400_000)
    assert expense_svc.shortfall(cls["class_id"], 300_000) == 200_000  # ждущие тоже в счёт
    expense_id = _new(cls, amount=600_000)
    expense_svc.approve(cls["class_id"], expense_id, cls["Ольга"])
    assert money.balance(cls["class_id"]) == -100_000


def test_back_dated_expense_counts_on_its_day(cls):
    day = util.today() - dt.timedelta(days=20)
    expense_id = expense_svc.create(
        cls["class_id"], cls["Мария"], title="Шары", amount=50_000, spent_at=day
    )
    expense_svc.approve(cls["class_id"], expense_id, cls["Ольга"])
    entry = db.one("SELECT occurred_at FROM ledger_entry WHERE source_type = 'expense'")
    assert util.local(entry["occurred_at"]).date() == day


@pytest.mark.parametrize(
    ("title", "amount", "days", "error"),
    [("  ", 1000, 0, "на что"), ("Шары", 0, 0, "больше нуля"), ("Шары", 1000, 1, "не наступила")],
)
def test_bad_expense_is_refused(cls, title, amount, days, error):
    with pytest.raises(expense_svc.ExpenseError, match=error):
        expense_svc.create(
            cls["class_id"], cls["Мария"], title=title, amount=amount,
            spent_at=util.today() + dt.timedelta(days=days),
        )


# ── API ─────────────────────────────────────────────────────────────────


def test_api_full_flow_with_receipt(cls, tg):
    client = TestClient(app)
    body = {
        "title": "Подарок учителю",
        "amount": 120_000,
        "spent_at": str(util.today()),
        "note": "цветы и торт",
        "receipt": base64.b64encode(JPEG).decode(),
    }
    assert client.post("/api/expenses", json=body, headers=auth(PARENT_TG)).status_code == 403
    response = client.post("/api/expenses", json=body, headers=auth(TREASURER_TG))
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["status"] == "pending" and created["receipt_id"]
    assert created["auditors_notified"] == 1

    # чек ушёл автору (ради file_id), карточка с чеком и кнопками — ревизору
    photo_to = [call.args[0] for call in tg.send_photo.await_args_list]
    assert photo_to == [TREASURER_TG, AUDITOR_TG]
    assert tg.send_photo.await_args_list[1].args[1] == "F-big"

    # пока не утверждён: родитель расхода не видит, комитет видит
    parent_view = client.get("/api/expenses", headers=auth(PARENT_TG)).json()
    assert parent_view["items"] == [] and parent_view["balance"] == 500_000
    assert len(client.get("/api/expenses", headers=auth(CHAIR_TG)).json()["items"]) == 1
    receipt = f"/api/files/{created['receipt_id']}"
    tg.get_file = AsyncMock(return_value=SimpleNamespace(file_path="p/1.jpg"))
    tg.download_file = AsyncMock(return_value=io.BytesIO(JPEG))
    assert client.get(receipt, headers=auth(PARENT_TG)).status_code == 403

    url = f"/api/expenses/{created['id']}/approve"
    assert client.post(url, headers=auth(CHAIR_TG)).status_code == 403  # только ревизор
    assert client.post(url, headers=auth(AUDITOR_TG)).status_code == 200

    parent_view = client.get("/api/expenses", headers=auth(PARENT_TG)).json()
    assert parent_view["balance"] == 380_000
    assert [e["approved_by_name"] for e in parent_view["items"]] == ["Ольга"]
    assert client.get(receipt, headers=auth(PARENT_TG)).status_code == 200  # отчёт — всем

    home = client.get("/api/home", headers=auth(AUDITOR_TG)).json()
    assert home["expenses_to_approve"] == 0


def test_api_rejects_non_image_receipt(cls, tg):
    client = TestClient(app)
    body = {
        "title": "Шары",
        "amount": 1000,
        "spent_at": str(util.today()),
        "receipt": base64.b64encode(b"<html>").decode(),
    }
    response = client.post("/api/expenses", json=body, headers=auth(TREASURER_TG))
    assert response.status_code == 400
    assert db.scalar("SELECT COUNT(*) FROM expense") == 0
    tg.send_photo.assert_not_awaited()


def test_api_hidden_from_teacher(cls):
    client = TestClient(app)
    assert client.get("/api/expenses", headers=auth(TEACHER_TG)).status_code == 403


def test_auditor_counter_on_home(cls):
    _new(cls)
    client = TestClient(app)
    assert client.get("/api/home", headers=auth(AUDITOR_TG)).json()["expenses_to_approve"] == 1
    assert "expenses_to_approve" not in client.get("/api/home", headers=auth(CHAIR_TG)).json()


# ── Кнопки ревизора в боте ──────────────────────────────────────────────


def _callback(data: str):
    message = SimpleNamespace(
        caption="💸 Расход", html_text="💸 Расход", edit_caption=AsyncMock(), edit_text=AsyncMock()
    )
    return SimpleNamespace(data=data, message=message, answer=AsyncMock())


async def test_auditor_approves_by_button_and_author_is_told(cls):
    expense_id = _new(cls)
    auditor = persons.by_id(cls["Ольга"])
    callback = _callback(f"econf:{expense_id}")
    await expense_bot.expense_approve(callback, auditor, {roles_mod.AUDITOR})

    assert money.balance(cls["class_id"]) == 380_000
    callback.message.edit_caption.assert_awaited()  # кнопки сняты
    note = db.one("SELECT * FROM notification WHERE person_id = ?", cls["Мария"])
    assert note["kind"] == "expense.approved"


async def test_chair_button_is_denied(cls):
    expense_id = _new(cls)
    chair = persons.by_id(cls["Сергей"])
    callback = _callback(f"econf:{expense_id}")
    await expense_bot.expense_approve(callback, chair, {roles_mod.PARENT, roles_mod.CHAIR})
    assert money.balance(cls["class_id"]) == 500_000
