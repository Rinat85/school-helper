"""Платежи: заявка родителя -> подтверждение казначея -> строка в журнале.

Деньги попадают в ledger_entry ТОЛЬКО через confirm(). Заявка родителя —
это ещё не поступление: пока казначей не увидел перевод, в кассе ничего нет.
"""

from __future__ import annotations

import sqlite3

from ..core import util
from ..storage import db, journal, money


class PaymentError(Exception):
    pass


def claim(
    person_id: int,
    collection_id: int,
    *,
    amount: int,
    method: str = "transfer",
    receipt_file_id: int | None = None,
    entered_by: int | None = None,
    idempotency_key: str | None = None,
) -> int:
    """Родитель заявил оплату. В кассу пока ничего не пишем."""
    contribution = db.one(
        "SELECT * FROM contribution WHERE collection_id = ? AND person_id = ?",
        collection_id,
        person_id,
    )
    if contribution is None:
        raise PaymentError("нет взноса для этого родителя в этом сборе")

    if idempotency_key:
        existing = db.one(
            "SELECT id FROM payment WHERE idempotency_key = ?", idempotency_key
        )
        if existing:
            return int(existing["id"])

    return db.insert(
        "payment",
        contribution_id=int(contribution["id"]),
        person_id=person_id,
        amount=amount,
        method=method,
        receipt_id=receipt_file_id,
        status="claimed",
        claimed_at=util.now_iso(),
        entered_by=entered_by,
        idempotency_key=idempotency_key,
    )


def confirm(payment_id: int, by_person_id: int) -> sqlite3.Row:
    """Казначей подтвердил. Здесь и только здесь деньги попадают в журнал."""
    payment = get(payment_id)
    if payment is None:
        raise PaymentError("платёж не найден")
    if payment["status"] == "confirmed":
        return payment
    if payment["status"] == "rejected":
        raise PaymentError("платёж уже отклонён")

    collection = db.one(
        "SELECT col.* FROM collection col "
        "JOIN contribution c ON c.collection_id = col.id "
        "WHERE c.id = ?",
        payment["contribution_id"],
    )
    if collection is None:
        raise PaymentError("сбор не найден")

    person = db.one("SELECT * FROM person WHERE id = ?", payment["person_id"])
    now = util.now_iso()

    with db.tx():
        db.execute(
            "UPDATE payment SET status = 'confirmed', confirmed_by = ?, confirmed_at = ? "
            "WHERE id = ?",
            by_person_id,
            now,
            payment_id,
        )
        money.add_entry(
            int(collection["class_id"]),
            "in",
            int(payment["amount"]),
            "payment",
            source_id=payment_id,
            case_id=collection["case_id"],
            memo=f"{collection['title']}",
            created_by=by_person_id,
        )
        # В хронике События взнос виден только money-ролям: в общей ленте
        # не должно быть видно, кто и когда платил.
        journal.log_case(
            collection["case_id"],
            "payment_confirmed",
            actor_id=by_person_id,
            ref_type="payment",
            ref_id=payment_id,
            text=f"Взнос {util.money(payment['amount'])} — "
            f"{person['display_name'] if person else ''}",
            visibility="money_roles",
        )
        journal.audit(
            int(collection["class_id"]),
            by_person_id,
            "payment.confirm",
            object_type="payment",
            object_id=payment_id,
            after={"amount": int(payment["amount"]), "person_id": int(payment["person_id"])},
        )

    result = get(payment_id)
    assert result is not None
    return result


def reject(payment_id: int, by_person_id: int, reason: str | None = None) -> sqlite3.Row:
    payment = get(payment_id)
    if payment is None:
        raise PaymentError("платёж не найден")
    if payment["status"] == "confirmed":
        raise PaymentError("платёж уже подтверждён — нужно сторно, а не отклонение")

    db.execute(
        "UPDATE payment SET status = 'rejected', confirmed_by = ?, confirmed_at = ?, "
        "reject_reason = ? WHERE id = ?",
        by_person_id,
        util.now_iso(),
        reason,
        payment_id,
    )
    result = get(payment_id)
    assert result is not None
    return result


def get(payment_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM payment WHERE id = ?", payment_id)


def pending(class_id: int) -> list[sqlite3.Row]:
    """Очередь казначея."""
    return db.query(
        "SELECT p.*, pe.display_name, col.title AS collection_title, col.id AS collection_id "
        "FROM payment p "
        "JOIN person pe ON pe.id = p.person_id "
        "JOIN contribution c ON c.id = p.contribution_id "
        "JOIN collection col ON col.id = c.collection_id "
        "WHERE col.class_id = ? AND p.status = 'claimed' "
        "ORDER BY p.claimed_at",
        class_id,
    )


def record_manual(
    person_id: int,
    collection_id: int,
    *,
    amount: int,
    method: str,
    by_person_id: int,
    idempotency_key: str | None = None,
) -> sqlite3.Row:
    """Казначей отмечает взнос сам — наличными или за родителя без Telegram.

    Заявка и подтверждение в одно действие: отдельно подтверждать то, что
    казначей сам же и внёс, бессмысленно. entered_by фиксирует, кто это сделал.
    """
    if amount <= 0:
        raise PaymentError("сумма должна быть больше нуля")
    payment_id = claim(
        person_id,
        collection_id,
        amount=amount,
        method=method,
        entered_by=by_person_id,
        idempotency_key=idempotency_key,
    )
    return confirm(payment_id, by_person_id)


def collection_of(payment_id: int) -> sqlite3.Row | None:
    return db.one(
        "SELECT col.* FROM collection col "
        "JOIN contribution c ON c.collection_id = col.id "
        "JOIN payment p ON p.contribution_id = c.id "
        "WHERE p.id = ?",
        payment_id,
    )
