"""API комитета: права, правила ролей, сборы и платежи через Mini App."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from helpers import make_init_data

from schoolhelper import config
from schoolhelper.app import app
from schoolhelper.core import roles as roles_mod
from schoolhelper.core import util
from schoolhelper.services import collections as coll_svc
from schoolhelper.services import payments as pay_svc
from schoolhelper.storage import files, money, persons


def auth(tg_id: int) -> dict:
    return {"Authorization": "tma " + make_init_data(tg_id)}


@pytest.fixture()
def cls(fresh_db):
    """Класс: председатель (1), казначей (2), родитель (3), учитель (4)."""
    class_id = fresh_db
    ids = {
        "chair": persons.create(class_id, "Сергей", tg_user_id=1, dm_open=True),
        "treasurer": persons.create(class_id, "Мария", tg_user_id=2, dm_open=True),
        "parent": persons.create(class_id, "Анна", tg_user_id=3, dm_open=True),
        "teacher": persons.create(class_id, "Гульнара", tg_user_id=4, dm_open=True),
    }
    now = util.now_iso()
    roles_mod.grant(ids["chair"], roles_mod.CHAIR, by=None, now=now)
    roles_mod.grant(ids["treasurer"], roles_mod.TREASURER, by=None, now=now)
    roles_mod.grant(ids["teacher"], roles_mod.TEACHER, by=None, now=now)
    return {"class_id": class_id, **ids}


@pytest.fixture()
def client(cls):
    return TestClient(app)


CHAIR, TREASURER, PARENT, TEACHER = auth(1), auth(2), auth(3), auth(4)


# ── Люди и роли ─────────────────────────────────────────────────────────


def test_people_list_is_for_chair_only(client):
    assert client.get("/api/people", headers=PARENT).status_code == 403
    assert client.get("/api/people", headers=TREASURER).status_code == 403
    body = client.get("/api/people", headers=CHAIR).json()
    assert {p["name"] for p in body} == {"Сергей", "Мария", "Анна", "Гульнара"}


def test_chair_grants_and_revokes_roles(client, cls):
    url = f"/api/people/{cls['parent']}/roles/auditor"
    assert client.put(url, json={"enabled": True}, headers=CHAIR).json()["roles"] == ["auditor"]
    assert client.put(url, json={"enabled": False}, headers=CHAIR).json()["roles"] == []


def test_parent_cannot_grant_roles(client, cls):
    url = f"/api/people/{cls['parent']}/roles/treasurer"
    assert client.put(url, json={"enabled": True}, headers=PARENT).status_code == 403


def test_admin_role_is_not_assignable(client, cls):
    url = f"/api/people/{cls['parent']}/roles/admin"
    response = client.put(url, json={"enabled": True}, headers=CHAIR)
    assert response.status_code == 400


def test_chair_cannot_demote_self(client, cls):
    url = f"/api/people/{cls['chair']}/roles/chair"
    response = client.put(url, json={"enabled": False}, headers=CHAIR)
    assert response.status_code == 400
    assert "самого себя" in response.json()["detail"]


def test_class_never_left_without_chair(client, cls):
    """Второй председатель снимает первого — можно; последнего снять нельзя."""
    roles_mod.grant(cls["parent"], roles_mod.CHAIR, by=None, now=util.now_iso())
    ok = client.put(
        f"/api/people/{cls['chair']}/roles/chair", json={"enabled": False}, headers=PARENT
    )
    assert ok.status_code == 200

    # теперь Анна — единственный председатель; снять её некому, кроме неё самой
    roles_mod.grant(cls["chair"], roles_mod.ADMIN, by=None, now=util.now_iso())
    last = client.put(
        f"/api/people/{cls['parent']}/roles/chair", json={"enabled": False}, headers=CHAIR
    )
    assert last.status_code == 400
    assert "хотя бы один" in last.json()["detail"]


def test_offline_parent_add_edit_leave(client):
    created = client.post(
        "/api/people", json={"name": "Бабушка Вани", "child": "Ваня"}, headers=CHAIR
    ).json()
    assert created["in_telegram"] is False

    edited = client.patch(
        f"/api/people/{created['id']}", json={"name": "Ольга", "child": "Ваня"}, headers=CHAIR
    ).json()
    assert edited["name"] == "Ольга"

    assert client.post(f"/api/people/{created['id']}/leave", headers=CHAIR).status_code == 200
    names = {p["name"] for p in client.get("/api/people", headers=CHAIR).json()}
    assert "Ольга" not in names


def test_invite_link_is_signed(client):
    body = client.get("/api/invite", headers=CHAIR).json()
    assert body["link"].startswith(f"https://t.me/{config.BOT_USERNAME}?start=join_")


# ── Настройки ───────────────────────────────────────────────────────────


def test_settings_split_by_responsibility(client):
    """Название меняет председатель, реквизиты — тот, кто объявляет сборы."""
    assert client.get("/api/settings", headers=PARENT).status_code == 403

    card = client.put(
        "/api/settings", json={"card_number": "8600 1234 5678 9012"}, headers=TREASURER
    )
    assert card.status_code == 200
    assert card.json()["card_number"] == "8600123456789012"

    rename = {"name": "2 «А»"}
    assert client.put("/api/settings", json=rename, headers=TREASURER).status_code == 403
    assert client.put("/api/settings", json=rename, headers=CHAIR).json()["name"] == "2 «А»"


# ── Сборы и платежи ─────────────────────────────────────────────────────


def _create(client, **extra) -> dict:
    body = {"title": "Цветы", "amount_per_person": 15_000, **extra}
    response = client.post("/api/collections", json=body, headers=TREASURER)
    assert response.status_code == 200, response.text
    return response.json()


def test_create_collection_announces_to_everyone(client):
    created = _create(client)
    assert created["status"] == "open"
    assert created["target"] == 15_000 * 4
    assert created["queued"] == 4          # личное сообщение каждому
    assert created["posted_to_group"] is False  # группа не привязана


def test_parent_cannot_create_collection(client):
    response = client.post(
        "/api/collections", json={"title": "x", "amount_per_person": 1}, headers=PARENT
    )
    assert response.status_code == 403


def test_past_due_date_rejected(client):
    response = client.post(
        "/api/collections",
        json={"title": "x", "amount_per_person": 1, "due_date": "2000-01-01"},
        headers=TREASURER,
    )
    assert response.status_code == 400


def test_roster_only_for_money_roles(client):
    """Родитель видит сбор и свой взнос, но не список «кто не сдал» (SPEC §2)."""
    collection_id = _create(client)["id"]
    parent_view = client.get(f"/api/collections/{collection_id}", headers=PARENT).json()
    assert "roster" not in parent_view
    assert parent_view["mine"] == {"expected": 15_000, "paid": 0, "status": "pending"}

    treasurer_view = client.get(f"/api/collections/{collection_id}", headers=TREASURER).json()
    assert len(treasurer_view["roster"]) == 4


def test_teacher_sees_no_collections(client):
    _create(client)
    assert client.get("/api/collections", headers=TEACHER).status_code == 403


def test_manual_payment_is_idempotent(client, cls):
    """Двойной тап «Отметить наличные» не должен задвоить деньги в кассе."""
    collection_id = _create(client)["id"]
    headers = {**TREASURER, "Idempotency-Key": "tap-1"}
    body = {"person_id": cls["parent"], "method": "cash"}

    client.post(f"/api/collections/{collection_id}/payments", json=body, headers=headers)
    client.post(f"/api/collections/{collection_id}/payments", json=body, headers=headers)

    assert money.balance(cls["class_id"]) == 15_000


def test_manual_payment_blocked_by_pending_claim(client, cls):
    """Родитель уже прислал чек — «сдал наличными» поверх него задвоило бы деньги."""
    collection_id = _create(client)["id"]
    pay_svc.claim(cls["parent"], collection_id, amount=15_000)
    response = client.post(
        f"/api/collections/{collection_id}/payments",
        json={"person_id": cls["parent"], "method": "cash"},
        headers=TREASURER,
    )
    assert response.status_code == 400
    assert "ждёт подтверждения" in response.json()["detail"]
    assert money.balance(cls["class_id"]) == 0


def test_only_treasurer_records_payments(client, cls):
    collection_id = _create(client)["id"]
    response = client.post(
        f"/api/collections/{collection_id}/payments",
        json={"person_id": cls["parent"]},
        headers=CHAIR,
    )
    assert response.status_code == 403


def test_pending_queue_confirm_and_reject(client, cls):
    collection_id = _create(client)["id"]
    first = pay_svc.claim(cls["parent"], collection_id, amount=15_000)
    second = pay_svc.claim(cls["chair"], collection_id, amount=15_000)

    queue = client.get("/api/payments/pending", headers=TREASURER).json()
    assert {p["id"] for p in queue} == {first, second}
    assert client.get("/api/payments/pending", headers=CHAIR).status_code == 403

    assert client.post(f"/api/payments/{first}/confirm", headers=TREASURER).json()[
        "status"
    ] == "confirmed"
    assert client.post(f"/api/payments/{second}/reject", headers=TREASURER).json()[
        "status"
    ] == "rejected"
    assert money.balance(cls["class_id"]) == 15_000
    assert client.get("/api/payments/pending", headers=TREASURER).json() == []


def test_waive_lowers_target(client, cls):
    collection_id = _create(client)["id"]
    url = f"/api/collections/{collection_id}/people/{cls['parent']}/waive"
    assert client.put(url, json={"waived": True}, headers=TREASURER).json()["target"] == 45_000
    assert client.put(url, json={"waived": False}, headers=TREASURER).json()["target"] == 60_000


def test_close_collection_once(client):
    collection_id = _create(client)["id"]
    assert client.post(f"/api/collections/{collection_id}/close", headers=TREASURER).json()[
        "status"
    ] == "closed"
    assert client.post(
        f"/api/collections/{collection_id}/close", headers=TREASURER
    ).status_code == 400


def test_home_shows_what_is_waiting(client, cls):
    collection_id = _create(client)["id"]
    pay_svc.claim(cls["parent"], collection_id, amount=15_000)

    treasurer_home = client.get("/api/home", headers=TREASURER).json()
    assert treasurer_home["payments_to_confirm"] == 1

    parent_home = client.get("/api/home", headers=PARENT).json()
    assert parent_home["my_open_contributions"][0]["status"] == "claimed"
    assert "payments_to_confirm" not in parent_home


# ── Чеки ────────────────────────────────────────────────────────────────


@pytest.fixture()
def receipt(cls, monkeypatch):
    monkeypatch.setattr(
        app.state.bot, "get_file", AsyncMock(return_value=SimpleNamespace(file_path="p/1.jpg"))
    )
    monkeypatch.setattr(
        app.state.bot, "download_file", AsyncMock(return_value=io.BytesIO(b"JPEG"))
    )
    return files.save_tg(cls["class_id"], "tg-file", kind="receipt", uploaded_by=cls["parent"])


def test_receipt_visible_to_uploader_and_treasurer(client, receipt):
    own = client.get(f"/api/files/{receipt}", headers=PARENT)
    assert own.status_code == 200 and own.content == b"JPEG"
    assert client.get(f"/api/files/{receipt}", headers=TREASURER).status_code == 200


def test_receipt_hidden_from_other_parents(client, receipt):
    assert client.get(f"/api/files/{receipt}", headers=TEACHER).status_code == 403


# ── Dev-вход ────────────────────────────────────────────────────────────


def test_dev_auth_rejected_from_non_local_host(client, monkeypatch):
    """Даже с заданной переменной dev-вход не работает не с localhost."""
    monkeypatch.setattr(config, "DEV_AUTH_TG_ID", 1)
    response = client.get("/api/me", headers={"Authorization": "dev"})
    assert response.status_code == 401


def test_dev_auth_works_from_localhost(cls, monkeypatch):
    """Локальная разработка Mini App в браузере: входим под заданным tg_user_id."""
    monkeypatch.setattr(config, "DEV_AUTH_TG_ID", 1)
    local = TestClient(app, client=("127.0.0.1", 50000))
    me = local.get("/api/me", headers={"Authorization": "dev"}).json()
    assert me["person"]["name"] == "Сергей"


def test_dev_auth_ignored_inside_docker(cls, monkeypatch):
    from schoolhelper.api import deps

    monkeypatch.setattr(config, "DEV_AUTH_TG_ID", 1)
    monkeypatch.setattr(deps, "_IN_DOCKER", True)
    local = TestClient(app, client=("127.0.0.1", 50000))
    assert local.get("/api/me", headers={"Authorization": "dev"}).status_code == 401


def test_dev_auth_off_by_default(cls):
    local = TestClient(app, client=("127.0.0.1", 50000))
    assert local.get("/api/me", headers={"Authorization": "dev"}).status_code == 401


def test_collection_service_unaffected(cls):
    """Санити: сервисный слой по-прежнему работает без HTTP."""
    collection_id = coll_svc.create(
        cls["class_id"], cls["treasurer"], title="x", amount_per_person=1
    )
    assert coll_svc.get(collection_id)["status"] == "draft"
