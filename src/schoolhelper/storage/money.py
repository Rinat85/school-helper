"""Денежный контур: журнал, остаток, прогресс сборов.

Инварианты (SPEC §4.4):
  * суммы — целые, в сумах;
  * остаток НИКОГДА не хранится полем, только считается по ledger_entry;
  * ledger_entry неизменяем: правка = сторно (встречная строка с reverses_id).

«Активной» считается строка, у которой нет сторно и которая сама не сторно.
Остаток и обороты считаются по одному и тому же множеству — иначе они разъедутся.
"""

from __future__ import annotations

import sqlite3

from ..core import util
from . import db, journal

ACTIVE = """
      e.reverses_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)
"""


# ── Журнал ──────────────────────────────────────────────────────────────


def add_entry(
    class_id: int,
    direction: str,
    amount: int,
    source_type: str,
    *,
    source_id: int | None = None,
    case_id: int | None = None,
    memo: str | None = None,
    created_by: int | None = None,
    occurred_at: str | None = None,
) -> int:
    if amount <= 0:
        raise ValueError("amount must be positive; direction carries the sign")
    return db.insert(
        "ledger_entry",
        class_id=class_id,
        case_id=case_id,
        occurred_at=occurred_at or util.now_iso(),
        direction=direction,
        amount=int(amount),
        source_type=source_type,
        source_id=source_id,
        memo=memo,
        created_by=created_by,
        created_at=util.now_iso(),
    )


def reverse(entry_id: int, by: int | None, memo: str) -> int:
    """Сторно. Удалять и править строки журнала нельзя."""
    src = db.one("SELECT * FROM ledger_entry WHERE id = ?", entry_id)
    if src is None:
        raise ValueError(f"ledger entry {entry_id} not found")
    if src["reverses_id"] is not None:
        raise ValueError("cannot reverse a reversal")
    if db.one("SELECT 1 FROM ledger_entry WHERE reverses_id = ?", entry_id):
        raise ValueError("entry already reversed")

    entry_id_new = db.insert(
        "ledger_entry",
        class_id=src["class_id"],
        case_id=src["case_id"],
        occurred_at=util.now_iso(),
        direction="out" if src["direction"] == "in" else "in",
        amount=src["amount"],
        source_type=src["source_type"],
        source_id=src["source_id"],
        memo=memo,
        created_by=by,
        created_at=util.now_iso(),
        reverses_id=entry_id,
    )
    journal.audit(
        src["class_id"], by, "ledger.reverse", object_type="ledger_entry", object_id=entry_id
    )
    return entry_id_new


def ledger(class_id: int, limit: int = 100) -> list[sqlite3.Row]:
    return db.query(
        f"SELECT e.* FROM ledger_entry e WHERE e.class_id = ? AND {ACTIVE} "
        "ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?",
        class_id,
        limit,
    )


# ── Остаток и обороты ───────────────────────────────────────────────────


def balance(class_id: int) -> int:
    return int(
        db.scalar(
            f"SELECT COALESCE(SUM(CASE e.direction WHEN 'in' THEN e.amount ELSE -e.amount END), 0) "
            f"FROM ledger_entry e WHERE e.class_id = ? AND {ACTIVE}",
            class_id,
        )
        or 0
    )


def turnover(class_id: int, since: str | None = None) -> tuple[int, int]:
    """(поступило, потрачено) за период."""
    where = f"e.class_id = ? AND {ACTIVE}"
    params: list = [class_id]
    if since:
        where += " AND e.occurred_at >= ?"
        params.append(since)
    row = db.one(
        f"SELECT COALESCE(SUM(CASE WHEN e.direction = 'in' THEN e.amount END), 0) AS inflow, "
        f"       COALESCE(SUM(CASE WHEN e.direction = 'out' THEN e.amount END), 0) AS outflow "
        f"FROM ledger_entry e WHERE {where}",
        *params,
    )
    return (int(row["inflow"]), int(row["outflow"])) if row else (0, 0)


def summary(class_id: int, since: str | None = None) -> dict:
    inflow, outflow = turnover(class_id, since)
    return {"balance": balance(class_id), "inflow": inflow, "outflow": outflow}


def case_totals(case_id: int) -> dict:
    """Итог События для хроники: собрано / потрачено / остаток."""
    row = db.one(
        f"SELECT COALESCE(SUM(CASE WHEN e.direction = 'in' THEN e.amount END), 0) AS collected, "
        f"       COALESCE(SUM(CASE WHEN e.direction = 'out' THEN e.amount END), 0) AS spent "
        f"FROM ledger_entry e WHERE e.case_id = ? AND {ACTIVE}",
        case_id,
    )
    collected = int(row["collected"]) if row else 0
    spent = int(row["spent"]) if row else 0
    return {"collected": collected, "spent": spent, "left": collected - spent}


# ── Сборы ───────────────────────────────────────────────────────────────


def collection_progress(collection_id: int) -> dict:
    """Публичная часть: сумма и цель. Имён здесь нет и быть не должно (SPEC §2)."""
    collected = int(
        db.scalar(
            "SELECT COALESCE(SUM(p.amount), 0) FROM payment p "
            "JOIN contribution c ON c.id = p.contribution_id "
            "WHERE c.collection_id = ? AND p.status = 'confirmed'",
            collection_id,
        )
        or 0
    )
    row = db.one(
        "SELECT COALESCE(SUM(expected), 0) AS target, COUNT(*) AS people "
        "FROM contribution WHERE collection_id = ? AND waived = 0",
        collection_id,
    )
    target = int(row["target"]) if row else 0
    override = db.scalar("SELECT total_target FROM collection WHERE id = ?", collection_id)
    if override:
        target = int(override)
    return {
        "collected": collected,
        "target": target,
        "people": int(row["people"]) if row else 0,
        "percent": round(100 * collected / target) if target else 0,
    }


def collection_roster(collection_id: int) -> list[sqlite3.Row]:
    """Поимённый разрез. Доступен ТОЛЬКО money-ролям — проверять на вызывающей стороне."""
    return db.query(
        """
        SELECT c.id            AS contribution_id,
               c.person_id,
               c.expected,
               c.waived,
               pe.display_name,
               pe.child_name,
               pe.dm_open,
               COALESCE(SUM(CASE WHEN p.status = 'confirmed' THEN p.amount END), 0) AS paid,
               MAX(CASE WHEN p.status = 'claimed' THEN 1 ELSE 0 END) AS has_claim
          FROM contribution c
          JOIN person pe ON pe.id = c.person_id
          LEFT JOIN payment p ON p.contribution_id = c.id
         WHERE c.collection_id = ?
      GROUP BY c.id
      ORDER BY pe.display_name
        """,
        collection_id,
    )


def contribution_status(row: sqlite3.Row) -> str:
    """confirmed | partial | claimed | waived | pending"""
    if row["waived"]:
        return "waived"
    paid = int(row["paid"] or 0)
    expected = int(row["expected"] or 0)
    if paid >= expected > 0:
        return "confirmed"
    if paid > 0:
        return "partial"
    if row["has_claim"]:
        return "claimed"
    return "pending"


def my_contributions(person_id: int) -> list[sqlite3.Row]:
    """Родитель видит только свои взносы."""
    return db.query(
        """
        SELECT col.id AS collection_id, col.title, col.due_date, col.status AS collection_status,
               c.id AS contribution_id, c.expected, c.waived,
               COALESCE(SUM(CASE WHEN p.status = 'confirmed' THEN p.amount END), 0) AS paid,
               MAX(CASE WHEN p.status = 'claimed' THEN 1 ELSE 0 END) AS has_claim
          FROM contribution c
          JOIN collection col ON col.id = c.collection_id
          LEFT JOIN payment p ON p.contribution_id = c.id
         WHERE c.person_id = ?
      GROUP BY c.id
      ORDER BY col.created_at DESC
        """,
        person_id,
    )
