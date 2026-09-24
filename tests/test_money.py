"""Денежные инварианты — то, что нельзя сломать никогда."""

from __future__ import annotations

import pytest

from schoolhelper.core import roles as roles_mod
from schoolhelper.services import collections as coll_svc
from schoolhelper.services import payments as pay_svc
from schoolhelper.storage import money


def test_claim_does_not_touch_the_ledger(parents):
    """Заявка родителя — ещё не поступление. В кассе до подтверждения пусто."""
    class_id = parents["class_id"]
    collection_id = coll_svc.create(
        class_id, parents["maria"], title="Канцтовары", amount_per_person=45_000
    )
    coll_svc.open_(collection_id, parents["maria"])

    pay_svc.claim(parents["anna"], collection_id, amount=45_000)

    assert money.balance(class_id) == 0
    assert money.collection_progress(collection_id)["collected"] == 0


def test_confirm_writes_to_the_ledger(parents):
    class_id = parents["class_id"]
    collection_id = coll_svc.create(
        class_id, parents["maria"], title="Канцтовары", amount_per_person=45_000
    )
    coll_svc.open_(collection_id, parents["maria"])

    payment_id = pay_svc.claim(parents["anna"], collection_id, amount=45_000)
    pay_svc.confirm(payment_id, parents["maria"])

    assert money.balance(class_id) == 45_000
    progress = money.collection_progress(collection_id)
    assert progress["collected"] == 45_000
    assert progress["target"] == 45_000 * 3  # трое активных родителей


def test_confirm_is_idempotent(parents):
    class_id = parents["class_id"]
    collection_id = coll_svc.create(
        class_id, parents["maria"], title="Цветы", amount_per_person=15_000
    )
    payment_id = pay_svc.claim(parents["anna"], collection_id, amount=15_000)

    pay_svc.confirm(payment_id, parents["maria"])
    pay_svc.confirm(payment_id, parents["maria"])  # двойной тап по кнопке

    assert money.balance(class_id) == 15_000


def test_reversal_cancels_the_entry(parents):
    """Правок в журнале нет — есть сторно, и оно обнуляет исходную строку."""
    class_id = parents["class_id"]
    entry_id = money.add_entry(
        class_id, "in", 100_000, "adjustment", memo="остаток с прошлого года"
    )
    assert money.balance(class_id) == 100_000

    money.reverse(entry_id, parents["sergey"], memo="ошибка ввода")
    assert money.balance(class_id) == 0

    inflow, outflow = money.turnover(class_id)
    assert inflow == 0 and outflow == 0  # обороты считаются по тому же множеству

    with pytest.raises(ValueError):
        money.reverse(entry_id, parents["sergey"], memo="ещё раз")


def test_amount_must_be_positive(parents):
    with pytest.raises(ValueError):
        money.add_entry(parents["class_id"], "in", -1, "adjustment")


def test_waived_parent_is_not_a_debtor(parents):
    """Освобождение уменьшает цель сбора и не оставляет статуса «должник»."""
    class_id = parents["class_id"]
    collection_id = coll_svc.create(
        class_id, parents["maria"], title="Экскурсия", amount_per_person=50_000
    )
    coll_svc.waive(collection_id, parents["anna"], parents["sergey"], note="по договорённости")

    assert money.collection_progress(collection_id)["target"] == 100_000

    roster = {row["person_id"]: money.contribution_status(row)
              for row in money.collection_roster(collection_id)}
    assert roster[parents["anna"]] == "waived"


def test_teacher_never_sees_money():
    """Учитель вне денежного контура — SPEC §3.2."""
    assert roles_mod.sees_money({roles_mod.TEACHER}) is False
    assert roles_mod.sees_money({roles_mod.PARENT}) is True
    assert roles_mod.has({roles_mod.TEACHER}, "money.view_by_person") is False


def test_only_money_roles_see_the_roster():
    assert roles_mod.has({roles_mod.PARENT}, "money.view_by_person") is False
    assert roles_mod.has({roles_mod.TREASURER}, "money.view_by_person") is True
    assert roles_mod.has({roles_mod.CHAIR}, "money.view_by_person") is True
    assert roles_mod.has({roles_mod.AUDITOR}, "money.view_by_person") is True


def test_expense_approval_is_not_available_to_its_author():
    """Правило двух рук: казначей создаёт расход, но утвердить его не может."""
    assert roles_mod.has({roles_mod.TREASURER}, "expense.create") is True
    assert roles_mod.has({roles_mod.TREASURER}, "expense.approve") is False
    assert roles_mod.has({roles_mod.CHAIR}, "expense.approve") is True


# ── Повторные заявки ────────────────────────────────────────────────────
# Найдено на живом тесте: «Я оплатил» под старым объявлением принимался и от
# того, чей взнос уже принят, — казначей мог подтвердить переплату.


def _open(parents, amount=50_000):
    collection_id = coll_svc.create(
        parents["class_id"], parents["maria"], title="Цветы", amount_per_person=amount
    )
    coll_svc.open_(collection_id, parents["maria"])
    return collection_id


def test_no_claim_after_contribution_is_paid(parents):
    collection_id = _open(parents)
    pay_svc.confirm(pay_svc.claim(parents["anna"], collection_id, amount=50_000), parents["maria"])

    with pytest.raises(pay_svc.PaymentError, match="уже принят"):
        pay_svc.claim(parents["anna"], collection_id, amount=50_000)


def test_no_second_claim_while_first_is_pending(parents):
    collection_id = _open(parents)
    pay_svc.claim(parents["anna"], collection_id, amount=50_000)

    with pytest.raises(pay_svc.PaymentError, match="ждёт подтверждения"):
        pay_svc.claim(parents["anna"], collection_id, amount=50_000)


def test_no_claim_when_waived(parents):
    collection_id = _open(parents)
    coll_svc.waive(collection_id, parents["anna"], parents["sergey"])

    with pytest.raises(pay_svc.PaymentError, match="освобождены"):
        pay_svc.claim(parents["anna"], collection_id, amount=50_000)


def test_partial_payer_claims_only_the_rest(parents):
    collection_id = _open(parents)
    pay_svc.record_manual(
        parents["anna"], collection_id, amount=20_000, method="cash", by_person_id=parents["maria"]
    )
    contribution = coll_svc.contribution_of(collection_id, parents["anna"])
    assert pay_svc.remaining(int(contribution["id"])) == 30_000

    with pytest.raises(pay_svc.PaymentError, match="осталось сдать"):
        pay_svc.claim(parents["anna"], collection_id, amount=50_000)
    pay_svc.claim(parents["anna"], collection_id, amount=30_000)  # остаток — можно


def test_rejected_claim_does_not_block_a_new_one(parents):
    """Казначей не нашёл перевод — родитель должен суметь прислать чек заново."""
    collection_id = _open(parents)
    first = pay_svc.claim(parents["anna"], collection_id, amount=50_000)
    pay_svc.reject(first, parents["maria"])
    pay_svc.claim(parents["anna"], collection_id, amount=50_000)
