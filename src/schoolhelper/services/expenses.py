"""Расходы: казначей вносит -> ревизор утверждает -> строка в журнале.

Правило двух рук без исключений: любой расход, на любую сумму, утверждает
ревизор, и утвердить свой же расход нельзя. Деньги уходят из кассы
(ledger_entry 'out') ТОЛЬКО в approve() — до этого расход лишь заявлен.

Расход больше остатка разрешён: бывает, что казначей заплатил из своих
и ждёт, пока соберут. Касса тогда уходит в минус, и это видно всем.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from ..core import util
from ..storage import db, journal, money

MAX_AMOUNT = 100_000_000


class ExpenseError(ValueError):
    """Нарушение правила — текст показывается пользователю как есть."""


def validate(title: str, amount: int, spent_at: dt.date) -> str:
    """Проверяет поля до загрузки чека в Telegram. Возвращает очищенное название."""
    title = " ".join(title.split())[:120]
    if not title:
        raise ExpenseError("напишите, на что потратили")
    if not 0 < amount <= MAX_AMOUNT:
        raise ExpenseError("сумма должна быть больше нуля")
    if spent_at > util.today():
        raise ExpenseError("дата расхода ещё не наступила")
    return title


def create(
    class_id: int,
    actor_id: int,
    *,
    title: str,
    amount: int,
    spent_at: dt.date,
    note: str | None = None,
    receipt_id: int | None = None,
) -> int:
    """Расход заявлен. Платил тот, кто вносит: так и бывает в комитете."""
    title = validate(title, amount, spent_at)
    with db.tx():
        expense_id = db.insert(
            "expense",
            class_id=class_id,
            title=title,
            amount=amount,
            spent_at=str(spent_at),
            paid_by=actor_id,
            receipt_id=receipt_id,
            status="pending",
            created_by=actor_id,
            note=(note or "").strip()[:500] or None,
            created_at=util.now_iso(),
        )
        journal.audit(
            class_id,
            actor_id,
            "expense.create",
            object_type="expense",
            object_id=expense_id,
            after={"title": title, "amount": amount, "spent_at": str(spent_at)},
        )
    return expense_id


def approve(class_id: int, expense_id: int, actor_id: int) -> sqlite3.Row:
    """Ревизор утвердил. Здесь и только здесь деньги уходят из кассы."""
    expense = _own(class_id, expense_id)
    if expense["status"] == "approved":
        raise ExpenseError("расход уже утверждён")
    if expense["status"] == "rejected":
        raise ExpenseError("расход уже отклонён")
    if int(expense["created_by"]) == actor_id:
        raise ExpenseError("свой расход утвердить нельзя — это делает другой человек")

    with db.tx():
        db.execute(
            "UPDATE expense SET status = 'approved', approved_by = ?, approved_at = ? "
            "WHERE id = ? AND status = 'pending'",
            actor_id,
            util.now_iso(),
            expense_id,
        )
        money.add_entry(
            class_id,
            "out",
            int(expense["amount"]),
            "expense",
            source_id=expense_id,
            case_id=expense["case_id"],
            memo=expense["title"],
            created_by=actor_id,
            occurred_at=_spent_moment(expense["spent_at"]),
        )
        journal.audit(
            class_id, actor_id, "expense.approve", object_type="expense", object_id=expense_id
        )
    return _own(class_id, expense_id)


def reject(class_id: int, expense_id: int, actor_id: int, reason: str | None) -> sqlite3.Row:
    expense = _own(class_id, expense_id)
    if expense["status"] == "approved":
        raise ExpenseError("расход уже утверждён")
    if expense["status"] == "rejected":
        raise ExpenseError("расход уже отклонён")
    if int(expense["created_by"]) == actor_id:
        raise ExpenseError("свой расход отклонить нельзя — это делает другой человек")

    reason = (reason or "").strip()[:200] or None
    note = expense["note"]
    if reason:
        note = "\n".join(filter(None, [note, f"Отклонено: {reason}"]))
    db.execute(
        "UPDATE expense SET status = 'rejected', approved_by = ?, approved_at = ?, note = ? "
        "WHERE id = ? AND status = 'pending'",
        actor_id,
        util.now_iso(),
        note,
        expense_id,
    )
    journal.audit(
        class_id,
        actor_id,
        "expense.reject",
        object_type="expense",
        object_id=expense_id,
        after={"reason": reason},
    )
    return _own(class_id, expense_id)


def get(expense_id: int) -> sqlite3.Row | None:
    return db.one(
        "SELECT e.*, c.display_name AS created_by_name, a.display_name AS approved_by_name "
        "FROM expense e "
        "LEFT JOIN person c ON c.id = e.created_by "
        "LEFT JOIN person a ON a.id = e.approved_by "
        "WHERE e.id = ?",
        expense_id,
    )


def listing(class_id: int, *, with_unapproved: bool, limit: int = 200) -> list[sqlite3.Row]:
    """Утверждённые видят все, кому виден денежный раздел; ждущие и отклонённые —
    только комитет: заявленный расход — ещё не факт."""
    where = "e.class_id = ?" + ("" if with_unapproved else " AND e.status = 'approved'")
    return db.query(
        "SELECT e.*, c.display_name AS created_by_name, a.display_name AS approved_by_name "
        "FROM expense e "
        "LEFT JOIN person c ON c.id = e.created_by "
        "LEFT JOIN person a ON a.id = e.approved_by "
        f"WHERE {where} "
        "ORDER BY CASE e.status WHEN 'pending' THEN 0 ELSE 1 END, e.spent_at DESC, e.id DESC "
        "LIMIT ?",
        class_id,
        limit,
    )


def pending_count(class_id: int, approver_id: int) -> int:
    """Сколько расходов ждёт именно этого человека: свои он утвердить не может."""
    return int(
        db.scalar(
            "SELECT COUNT(*) FROM expense WHERE class_id = ? AND status = 'pending' "
            "AND created_by != ?",
            class_id,
            approver_id,
        )
        or 0
    )


def shortfall(class_id: int, amount: int) -> int:
    """На сколько касса уйдёт в минус, если утвердить ещё и этот расход."""
    pending = int(
        db.scalar(
            "SELECT COALESCE(SUM(amount), 0) FROM expense "
            "WHERE class_id = ? AND status = 'pending'",
            class_id,
        )
        or 0
    )
    return max(pending + amount - money.balance(class_id), 0) if amount else 0


def receipt_visible_to_all(file_id: int) -> bool:
    """Чек утверждённого расхода — часть отчёта перед родителями."""
    return bool(
        db.scalar(
            "SELECT 1 FROM expense WHERE receipt_id = ? AND status = 'approved' LIMIT 1", file_id
        )
    )


def as_dict(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "amount": int(row["amount"]),
        "spent_at": row["spent_at"],
        "status": row["status"],
        "note": row["note"],
        "receipt_id": int(row["receipt_id"]) if row["receipt_id"] else None,
        "created_by": int(row["created_by"]) if row["created_by"] else None,
        "created_by_name": row["created_by_name"],
        "approved_by_name": row["approved_by_name"],
        "approved_at": row["approved_at"],
        "created_at": row["created_at"],
    }


def _own(class_id: int, expense_id: int) -> sqlite3.Row:
    row = get(expense_id)
    if row is None or int(row["class_id"]) != class_id:
        raise ExpenseError("расход не найден")
    return row


def _spent_moment(spent_at: str) -> str:
    """Расход в журнале — днём, когда потратили, а не когда утвердили:
    иначе обороты за месяц разъедутся с чеками."""
    moment = dt.datetime.combine(dt.date.fromisoformat(spent_at), dt.time(12), tzinfo=util.tz())
    return moment.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
