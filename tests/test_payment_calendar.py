"""Месяц обучения отделён от календарного окна, старые даты сохранены."""
import importlib.util
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.payment import Payment, STATUS_PENDING, STATUS_PAID
from app.services import payments as ps
from test_payments import payer, prices, prodamus, _msk


@pytest.mark.parametrize('window,opens,cutoff', [
    ((25, 30), _msk(2026, 11, 25), _msk(2026, 12, 1, 2)),
    ((10, 15), _msk(2026, 12, 10), _msk(2026, 12, 16, 2)),
])
def test_paid_november_next_payment_is_december(payer, window, opens, cutoff):
    payer.pay_window_start, payer.pay_window_end = window
    payer.paid_until = ps.paid_until_after(payer, date(2026, 11, 1))
    assert payer.paid_until == cutoff
    assert ps.paid_month(payer) == date(2026, 11, 1)
    assert ps.due_period(payer, _msk(2026, 11, 11)) == date(2026, 12, 1)
    assert ps.window_opens_at(payer, date(2026, 12, 1)) == opens
    assert ps.can_pay_now(payer, opens)


def test_december_window_pays_january_and_custom_window_unchanged(payer):
    payer.pay_window_start, payer.pay_window_end = 25, 30
    assert ps.window_opens_at(payer, date(2027, 1, 1)) == _msk(2026, 12, 25)
    payer.pay_window_start, payer.pay_window_end = 25, 31
    assert ps.window_opens_at(payer, date(2027, 1, 1)) == _msk(2027, 1, 25)


def test_first_purchase_changes_month_only_at_window_open(payer):
    payer.pay_window_start, payer.pay_window_end = 25, 30
    assert ps.due_period(payer, _msk(2026, 11, 24, 23, 59)) == date(2026, 11, 1)
    assert ps.due_period(payer, _msk(2026, 11, 25)) == date(2026, 12, 1)


def test_manual_old_cutoff_preserved_and_save_is_idempotent(db, payer):
    payer.pay_window_start, payer.pay_window_end = 25, 30
    # Старое «оплачен октябрь» с окном 25–30 ноября теперь называется ноябрём.
    payer.paid_until = _msk(2026, 12, 1, 2)
    assert ps.paid_month(payer) == date(2026, 11, 1)
    view = ps.manage_view(db, payer)
    assert view['paid_month'] == '2026-11'
    assert view['next_payment_month'] == 'декабрь 2026'
    assert view['next_window_start'] == '25.11.2026'
    assert view['next_window_end'] == '30.11.2026'
    ps.apply_payment_settings(db, payer.id, payer, window_start=25,
                             window_end=30, cohort=payer.pay_cohort,
                             price_kop=payer.pay_price_kop, paid_through=date(2026, 11, 1))
    assert payer.paid_until == _msk(2026, 12, 1, 2)


def _migrate(db):
    path = Path(__file__).parents[1] / 'alembic/versions/f31b7c9d204e_payment_service_month.py'
    spec = importlib.util.spec_from_file_location('calendar_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = SimpleNamespace(get_bind=lambda: db.connection())
    module.upgrade()
    db.expire_all()


def test_migration_preserves_legacy_pending_link_webhook_and_refund(db, payer, prices, prodamus):
    payer.pay_window_start, payer.pay_window_end = 25, 30
    payer.paid_until = _msk(2026, 12, 1, 2)
    old_until = payer.paid_until
    payment = Payment(user_id=payer.id, period=date(2026, 11, 1), amount_kop=1325500,
                      tariff=payer.tariff, status=STATUS_PENDING,
                      link_expires_at=_msk(2026, 12, 1, 2))
    db.add(payment); db.flush()
    payment.link_url = ps.build_link(payment, payer, 'https://testserver')
    original_link = payment.link_url
    order = ps.order_number(payment)
    db.flush()
    _migrate(db)
    assert payment.period == date(2026, 12, 1)
    assert payment.link_url == original_link
    assert ps._as_utc(payer.paid_until) == old_until
    assert ps.get_or_create_payment(db, payer, _msk(2026, 11, 26), 'https://testserver').id == payment.id
    note = payment.note
    _migrate(db)
    assert payment.period == date(2026, 12, 1) and payment.note == note
    body = {'order_num': order, 'order_id': 'calendar-test', 'payment_status': 'success', 'sum': '13255.00'}
    assert ps.process_webhook(db, body, _msk(2026, 11, 26)).status_code == 200
    db.flush()
    assert payment.status == STATUS_PAID
    # Старый ноябрьский платёж давал доступ до декабрьского окна; это сохранено.
    assert ps._as_utc(payer.paid_until) == _msk(2026, 12, 31, 2)
    assert ps.process_webhook(db, body, _msk(2026, 11, 26)).status_code == 200
    assert db.query(Payment).count() == 1
    ps.revert_paid(db, payment, refund=True, performed_by_id=payer.id, now=_msk(2026, 11, 27))
    assert ps._as_utc(payer.paid_until) == old_until


def test_migration_leaves_current_window_and_holiday_orders(db, payer):
    payment = Payment(user_id=payer.id, period=date(2026, 11, 1), amount_kop=100,
                      tariff=payer.tariff, status=STATUS_PENDING)
    db.add(payment); db.flush()
    _migrate(db)
    assert payment.period == date(2026, 11, 1)
    payer.pay_window_start, payer.pay_window_end = 25, 30
    payment.kind = 'holiday'; db.flush()
    _migrate(db)
    assert payment.period == date(2026, 11, 1)
