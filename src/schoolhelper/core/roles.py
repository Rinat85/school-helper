"""Роли и права (SPEC §3).

Роли живут в person_role с историей (granted_at / revoked_at): смена председателя —
новая запись, а не UPDATE. Право проверяется только на сервере и только по БД.
"""

from __future__ import annotations

from ..storage import db

PARENT = "parent"
TREASURER = "treasurer"
CHAIR = "chair"
AUDITOR = "auditor"
TEACHER = "teacher"
ADMIN = "admin"

ALL_ROLES = (PARENT, TREASURER, CHAIR, AUDITOR, TEACHER, ADMIN)

# Роли, которым вообще доступен денежный контур.
# Учителя здесь нет и быть не должно — см. SPEC §3.2.
MONEY_ROLES = frozenset({TREASURER, CHAIR, AUDITOR, ADMIN})

RU = {
    PARENT: "родитель",
    TREASURER: "казначей",
    CHAIR: "председатель",
    AUDITOR: "ревизор",
    TEACHER: "учитель",
    ADMIN: "админ",
}

# Право -> роли, которым оно выдано.
PERMISSIONS: dict[str, frozenset[str]] = {
    # деньги
    "money.view_summary":   frozenset({PARENT, TREASURER, CHAIR, AUDITOR, ADMIN}),
    "money.view_by_person": frozenset({TREASURER, CHAIR, AUDITOR}),
    "collection.create":    frozenset({TREASURER, CHAIR}),
    "payment.confirm":      frozenset({TREASURER}),
    "expense.create":       frozenset({TREASURER, CHAIR}),
    "expense.approve":      frozenset({CHAIR, AUDITOR}),
    # решения и события
    "poll.create":          frozenset({TREASURER, CHAIR}),
    "case.create":          frozenset({TREASURER, CHAIR}),
    "case.close":           frozenset({CHAIR}),
    # школьный контур
    "announcement.publish": frozenset({TEACHER, CHAIR}),
    "timetable.edit":       frozenset({TEACHER, CHAIR}),
    "announcement.view_acks_by_person": frozenset({TEACHER, CHAIR}),
    # служебное
    "audit.view":           frozenset({CHAIR, AUDITOR, ADMIN}),
    "role.grant":           frozenset({CHAIR, ADMIN}),
    "person.manage":        frozenset({CHAIR, ADMIN}),
    "class.edit":           frozenset({CHAIR, ADMIN}),
}


class Forbidden(Exception):
    """Не хватает прав."""

    def __init__(self, permission: str) -> None:
        super().__init__(f"permission denied: {permission}")
        self.permission = permission


def roles_of(person_id: int) -> set[str]:
    rows = db.query(
        "SELECT role FROM person_role WHERE person_id = ? AND revoked_at IS NULL",
        person_id,
    )
    return {r["role"] for r in rows}


def has(roles: set[str], permission: str) -> bool:
    allowed = PERMISSIONS.get(permission)
    if allowed is None:
        raise KeyError(f"unknown permission: {permission}")
    # ADMIN не получает права автоматически: денежные операции он тоже
    # не проводит в одиночку. Права выдаются только явно, через PERMISSIONS.
    return bool(roles & allowed)


def require(roles: set[str], permission: str) -> None:
    if not has(roles, permission):
        raise Forbidden(permission)


def sees_money(roles: set[str]) -> bool:
    """Учителю денежный контур не показывается вообще."""
    if TEACHER in roles and not (roles & MONEY_ROLES):
        return False
    return has(roles, "money.view_summary")


def grant(person_id: int, role: str, by: int | None, now: str) -> None:
    if role not in ALL_ROLES:
        raise ValueError(f"unknown role: {role}")
    if role in roles_of(person_id):
        return
    db.insert(
        "person_role",
        person_id=person_id,
        role=role,
        granted_by=by,
        granted_at=now,
    )


def revoke(person_id: int, role: str, by: int | None, now: str) -> None:
    db.execute(
        "UPDATE person_role SET revoked_at = ?, revoked_by = ? "
        "WHERE person_id = ? AND role = ? AND revoked_at IS NULL",
        now,
        by,
        person_id,
        role,
    )
