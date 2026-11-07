"""Публичная покупка: договор доступа от оплаты до вступления в канал."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import settings
from app.constants import TARIFFS_CURRENT
from app.models.payment import COHORT_FROM, Payment, PaymentPrice, STATUS_PAID
from app.models.public_enrollment import PublicEnrollment
from app.models.user import User
from app.services import payments, public_purchase

TARIFF = TARIFFS_CURRENT[0]
INVITE = 'https://t.me/+test-private-invitation'


@pytest.fixture
def sales(db, monkeypatch):
    for key, value in {
        'public_purchase_enabled': True, 'public_purchase_window_start': 25,
        'public_purchase_window_end': 30, 'public_purchase_invite_url': INVITE,
        'prodamus_form_url': 'https://purchase-test.payform.ru/',
        'prodamus_secret_key': 'test-only-key', 'telegram_login_client_id': 'test-id',
        'telegram_login_client_secret': 'test-secret',
        'domain': 'testserver',
        'telegram_bot_token': 'test-bot', 'telegram_channel_id': -10012345,
    }.items():
        monkeypatch.setattr(settings, key, value)
    db.add(PaymentPrice(tariff=TARIFF, cohort=COHORT_FROM, amount_kop=100000))
    db.commit()


@pytest.fixture
def buyer(client, db, user_factory, session_factory, sales):
    student = user_factory(tariff='', is_group_member=False, profile_completed=False)
    student.telegram_chat_id = 999111222
    enrollment = PublicEnrollment(user_id=student.id, selected_tariff=TARIFF)
    db.add(enrollment)
    db.commit()
    session = session_factory(student)
    client.cookies.set('session_id', session.id)
    return student, enrollment


def _payment(db, buyer):
    return public_purchase.checkout(db, buyer[0], 'https://testserver', datetime.now(timezone.utc))


def _settle(db, payment, *, amount='1000.00', order_id='purchase-1'):
    data = {'order_num': payments.order_number(payment), 'order_id': order_id,
            'payment_status': 'success', 'sum': amount}
    result = payments.process_webhook(db, data, datetime.now(timezone.utc))
    db.commit()
    return result


def _profile():
    return dict(first_name='Анна', last_name='Смирнова', birth_date='2010-05-20',
                city='Москва', timezone='0', phone='+79001112233', parent_phone='+79002223344',
                parent_name='Ольга Викторовна', sdek_address='Москва, ул. Ленина, 1',
                email='anna@example.com', tg_username='anna_art', university_year='2027')


def _oauth(client, monkeypatch, *, chat_id=999111222, purpose='purchase', redis=False):
    import app.api.auth as auth
    payload = {'cv': 'verifier', 'st': 'test-state', 'purpose': purpose, 'purchase_tariff': TARIFF}
    client.cookies.set('tg_pkce_cv', auth._signer.dumps(payload))
    monkeypatch.setattr(auth, 'pop_telegram_oidc_pkce', lambda _: (
        {'code_verifier': 'verifier', 'purpose': purpose, 'purchase_tariff': TARIFF} if redis else None))

    async def exchange(*args):
        return {'id_token': 'test-token'}

    monkeypatch.setattr(auth, 'tg_exchange_code', exchange)
    monkeypatch.setattr(auth, 'tg_verify_id_token', lambda _: {
        'id': chat_id, 'preferred_username': 'anna_art', 'given_name': 'Анна', 'family_name': 'Смирнова'})
    return client.get('/auth/telegram-login/callback?code=ok&state=test-state', follow_redirects=False)


@pytest.mark.parametrize('key,value', [
    ('public_purchase_enabled', False), ('public_purchase_window_start', 0),
    ('public_purchase_window_end', 32), ('public_purchase_window_start', 31),
    ('public_purchase_invite_url', ''), ('public_purchase_invite_url', 'javascript:alert(1)'),
    ('public_purchase_invite_url', 'https://evil.example/'), ('prodamus_secret_key', ''),
    ('telegram_bot_token', ''), ('telegram_channel_id', 0),
])
def test_incomplete_settings_disable_orders(client, db, buyer, monkeypatch, key, value):
    monkeypatch.setattr(settings, key, value)
    response = client.post('/purchase/pay')
    assert response.status_code == 200
    assert 'Приём оплат пока закрыт' in response.text
    assert db.query(Payment).count() == 0


def test_catalog_uses_current_tariffs_and_new_cohort(client, db, sales):
    db.add(PaymentPrice(tariff=TARIFF, cohort='before_0901', amount_kop=50000))
    db.add(PaymentPrice(tariff='УВЕРЕННЫЙ', cohort=COHORT_FROM, amount_kop=990000))
    db.add(PaymentPrice(tariff=TARIFFS_CURRENT[1], cohort=COHORT_FROM, amount_kop=0))
    db.commit()
    assert public_purchase.catalog(db) == [{'tariff': TARIFF, 'amount_kop': 100000}]
    response = client.get('/tariffs')
    assert '1 000 ₽' in response.text
    assert INVITE not in response.text


@pytest.mark.parametrize('redis', [False, True])
def test_oauth_creates_restricted_buyer_without_channel(client, db, sales, monkeypatch, redis):
    async def forbidden_check(*args):
        pytest.fail('Membership must not be checked during purchase registration')
    import app.api.auth as auth
    monkeypatch.setattr(auth.telegram_service, 'check_channel_membership', forbidden_check)
    response = _oauth(client, monkeypatch, redis=redis)
    assert response.status_code == 302
    assert response.headers['location'] == '/purchase'
    student = db.query(User).filter_by(telegram_chat_id=999111222).one()
    assert not student.is_group_member
    assert student.tariff == ''
    assert student.program_access_from is None
    assert public_purchase.enrollment_for(db, student.id) is not None
    response = _oauth(client, monkeypatch, purpose='login', redis=redis)
    assert response.headers['location'] == '/purchase'
    assert db.query(User).count() == 1


def test_oauth_does_not_enroll_existing_student(client, db, sales, user_factory, monkeypatch):
    student = user_factory()
    student.telegram_chat_id = 999111222
    db.commit()
    async def member(*args):
        return True
    import app.api.auth as auth
    monkeypatch.setattr(auth.telegram_service, 'check_channel_membership', member)
    response = _oauth(client, monkeypatch)
    assert response.headers['location'] == '/cabinet'
    assert db.query(PublicEnrollment).count() == 0
    assert student.tariff == 'УВЕРЕННЫЙ'


@pytest.mark.parametrize('path', ['/cabinet', '/cabinet/learning', '/cabinet/personal',
                                  '/cabinet/profile', '/cabinet/videos/1/embed', '/lab/asset/file.glb'])
def test_unpaid_cannot_open_other_routes(client, buyer, path):
    response = client.get(path, headers={'Accept': 'application/json'}, follow_redirects=False)
    assert response.status_code == 403
    assert response.json()['detail'] == public_purchase.PURCHASE_REQUIRED


def test_two_clicks_reuse_pending_payment_and_do_not_assign_tariff(client, db, buyer):
    first = client.post('/purchase/pay', follow_redirects=False)
    second = client.post('/purchase/pay', follow_redirects=False)
    assert first.status_code == second.status_code == 303
    assert first.headers['location'] == second.headers['location']
    assert db.query(Payment).count() == 1
    assert buyer[0].tariff == ''
    params = parse_qs(urlparse(first.headers['location']).query)
    assert params['urlSuccess'] == ['https://testserver/purchase?paid=1']
    assert INVITE not in first.headers['location']


def test_forged_success_return_does_not_open_profile_or_invitation(client, buyer):
    response = client.get('/purchase?paid=1')
    assert INVITE not in response.text
    assert 'Заполнить анкету' not in response.text
    assert client.get('/cabinet/profile', headers={'Accept': 'application/json'}).status_code == 403


def test_paid_buyer_can_only_fill_annual_profile_then_get_invitation(client, db, buyer):
    payment = _payment(db, buyer)
    _settle(db, payment)
    assert buyer[0].tariff == TARIFF
    assert buyer[1].first_payment_id == payment.id
    response = client.get('/purchase')
    assert public_purchase.AFTER_PAYMENT_TEXT in response.text
    assert INVITE not in response.text
    profile = client.get('/cabinet/profile')
    assert profile.status_code == 200
    assert 'Оплаченный тариф:' in profile.text
    assert 'id="tariff-select"' not in profile.text
    assert client.get('/cabinet/learning', headers={'Accept': 'application/json'}).status_code == 403
    payload = _profile()
    payload['tariff'] = TARIFFS_CURRENT[-1]
    response = client.post('/cabinet/profile', data=payload, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['location'] == '/purchase'
    assert buyer[0].profile_completed
    assert buyer[0].tariff == TARIFF
    assert INVITE in client.get('/purchase').text
    assert client.get('/cabinet/learning', headers={'Accept': 'application/json'}).status_code == 403
    assert client.post('/cabinet/profile', data=payload, follow_redirects=False).headers['location'] == '/purchase'


def test_invalid_profile_does_not_reveal_invitation(client, db, buyer):
    _settle(db, _payment(db, buyer))
    payload = _profile()
    payload['email'] = ''
    response = client.post('/cabinet/profile', data=payload)
    assert response.status_code == 200
    assert not buyer[0].profile_completed
    assert INVITE not in client.get('/purchase').text


@pytest.mark.parametrize('membership', [False, None, True])
def test_channel_admission_checks_all_conditions_and_is_idempotent(client, db, buyer, monkeypatch, membership):
    _settle(db, _payment(db, buyer))
    buyer[0].profile_completed = True
    db.commit()
    from app.api import public_purchase as routes
    async def check(*args):
        return membership
    monkeypatch.setattr(routes.telegram, 'check_channel_membership', check)
    response = client.post('/purchase/check-channel', follow_redirects=False)
    if membership is True:
        assert response.status_code == 303
        assert buyer[1].activated_at is not None
        assert buyer[0].program_access_from is not None
        first = buyer[1].activated_at
        response = client.post('/purchase/check-channel', follow_redirects=False)
        assert response.status_code == 303
        assert buyer[1].activated_at == first
        from app.models.audit_log import AuditLog
        assert db.query(AuditLog).filter_by(action='public_purchase_activated').count() == 1
    else:
        assert response.status_code == 200
        assert buyer[1].activated_at is None
        assert not buyer[0].is_group_member


def test_unpaid_channel_check_does_not_call_telegram(client, buyer, monkeypatch):
    from app.api import public_purchase as routes
    async def check(*args):
        pytest.fail('No Telegram request before payment and profile')
    monkeypatch.setattr(routes.telegram, 'check_channel_membership', check)
    assert client.post('/purchase/check-channel').status_code == 200
    assert buyer[1].activated_at is None


def test_refund_before_admission_hides_invite_and_blocks_profile(client, db, buyer):
    payment = _payment(db, buyer)
    _settle(db, payment)
    buyer[0].profile_completed = True
    db.commit()
    payments.revert_paid(db, payment, refund=True, performed_by_id=buyer[0].id,
                         now=datetime.now(timezone.utc))
    db.commit()
    assert INVITE not in client.get('/purchase').text
    assert client.get('/cabinet/profile', headers={'Accept': 'application/json'}).status_code == 403
    assert not public_purchase.activate(db, buyer[0].id, datetime.now(timezone.utc))


def test_signed_webhook_and_replay_do_not_duplicate_enrollment(client, db, buyer, monkeypatch):
    payment = _payment(db, buyer)
    db.commit()
    data = {'order_num': payments.order_number(payment), 'order_id': 'verified-order',
            'payment_status': 'success', 'sum': '1000.00'}
    from app.api import payments as routes
    async def quiet(*args):
        pass
    monkeypatch.setattr(routes, 'notify_many', quiet)
    assert client.post('/payments/prodamus/webhook', data=data, headers={'Sign': 'wrong'}).status_code == 403
    assert buyer[1].first_payment_id is None
    headers = {'Sign': payments.prodamus_sign(data, settings.prodamus_secret_key)}
    assert client.post('/payments/prodamus/webhook', data=data, headers=headers).status_code == 200
    from app.models.notification import Notification
    count = db.query(Notification).count()
    assert client.post('/payments/prodamus/webhook', data=data, headers=headers).status_code == 200
    assert db.query(Notification).count() == count
    assert buyer[1].first_payment_id == payment.id
    assert buyer[1].activated_at is None


def test_wrong_sum_does_not_assign_tariff_or_admit(client, db, buyer):
    payment = _payment(db, buyer)
    _settle(db, payment, amount='1.00')
    assert buyer[0].tariff == ''
    assert buyer[1].first_payment_id is None
    assert INVITE not in client.get('/purchase').text


def test_csrf_is_required_for_both_purchase_posts(client, buyer):
    from app.main import app
    from app.dependencies import require_csrf
    app.dependency_overrides.pop(require_csrf)
    for path in ['/purchase/pay', '/purchase/check-channel']:
        response = client.post(path, headers={'Accept': 'application/json'})
        assert response.status_code == 403
        assert 'CSRF' in response.json()['detail']


def test_first_payment_uses_current_month_and_next_window(db, buyer):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    payment = public_purchase.checkout(db, buyer[0], 'https://testserver', now)
    assert payment.period.isoformat() == '2026-11-01'
    _settle(db, payment)
    assert payments._as_utc(buyer[0].paid_until).month == 12
    assert payments.due_period(buyer[0], now).isoformat() == '2026-12-01'


def test_expired_payment_cannot_admit_even_with_membership(db, buyer):
    _settle(db, _payment(db, buyer))
    buyer[0].profile_completed = buyer[0].is_group_member = True
    buyer[0].paid_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert not public_purchase.activate(db, buyer[0].id, datetime.now(timezone.utc))


def test_refund_during_telegram_check_cannot_admit(client, db, buyer, monkeypatch):
    payment = _payment(db, buyer)
    _settle(db, payment)
    buyer[0].profile_completed = True
    db.commit()
    from sqlalchemy.orm import Session
    from app.api import public_purchase as routes
    async def refund_while_waiting(*args):
        with Session(db.get_bind()) as remote:
            other_payment = remote.get(Payment, payment.id)
            other_payment.status = 'refunded'
            remote.commit()
        return True
    monkeypatch.setattr(routes.telegram, 'check_channel_membership', refund_while_waiting)
    response = client.post('/purchase/check-channel', follow_redirects=False)
    assert response.status_code == 200
    assert buyer[1].activated_at is None
    assert INVITE not in response.text


def test_block_during_telegram_check_cannot_admit(client, db, buyer, monkeypatch):
    _settle(db, _payment(db, buyer))
    buyer[0].profile_completed = True
    db.commit()
    from sqlalchemy.orm import Session
    from app.api import public_purchase as routes
    async def block_while_waiting(*args):
        with Session(db.get_bind()) as remote:
            student = remote.get(User, buyer[0].id)
            student.is_active = False
            remote.commit()
        return True
    monkeypatch.setattr(routes.telegram, 'check_channel_membership', block_while_waiting)
    client.post('/purchase/check-channel', follow_redirects=False)
    assert buyer[1].activated_at is None


def test_payment_uses_paid_order_tariff_even_after_link_replacement(db, buyer):
    first = _payment(db, buyer)
    second_tariff = TARIFFS_CURRENT[1]
    db.add(PaymentPrice(tariff=second_tariff, cohort=COHORT_FROM, amount_kop=100000))
    buyer[1].selected_tariff = second_tariff
    db.commit()
    second = _payment(db, buyer)
    assert first.id != second.id
    _settle(db, first)
    assert buyer[0].tariff == TARIFF
    _settle(db, second, order_id='purchase-2')
    assert buyer[0].tariff == TARIFF
    assert buyer[1].first_payment_id == first.id


@pytest.mark.parametrize('field', ['is_active', 'deleted_at'])
def test_unavailable_buyer_cannot_read_invite(client, db, buyer, field):
    _settle(db, _payment(db, buyer))
    buyer[0].profile_completed = True
    setattr(buyer[0], field, False if field == 'is_active' else datetime.now(timezone.utc))
    db.commit()
    response = client.get('/purchase', headers={'Accept': 'application/json'})
    assert response.status_code == 403
    assert INVITE not in response.text


def test_other_student_cannot_get_buyers_invitation(client, db, buyer, user_factory, session_factory):
    _settle(db, _payment(db, buyer))
    buyer[0].profile_completed = True
    db.commit()
    other = user_factory(vk_id=100002)
    client.cookies.clear()
    client.cookies.set('session_id', session_factory(other).id)
    response = client.get('/purchase', follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['location'] == '/cabinet'
    assert INVITE not in response.text


def test_migration_upgrade_and_downgrade_on_empty_schema():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/f7b31e0c942a_public_enrollment.py'
    spec = importlib.util.spec_from_file_location('public_enrollment_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert 'public_enrollments' in inspect(conn).get_table_names()
        assert inspect(conn).get_pk_constraint('public_enrollments')['constrained_columns'] == ['user_id']
        migration.downgrade()
        assert 'public_enrollments' not in inspect(conn).get_table_names()
    engine.dispose()


def test_pending_buyer_does_not_receive_learning_reminders(db, buyer):
    from app.services.access_state import access_open_clause
    def recipients():
        return [row.id for row in db.query(User).filter(access_open_clause(datetime.now(timezone.utc)))]
    assert buyer[0].id not in recipients()
    _settle(db, _payment(db, buyer))
    buyer[0].profile_completed = True
    db.commit()
    assert buyer[0].id not in recipients()
    buyer[0].is_group_member = True
    db.flush()
    assert public_purchase.activate(db, buyer[0].id, datetime.now(timezone.utc))
    db.commit()
    assert buyer[0].id in recipients()


def test_sso_cannot_bypass_enrollment_even_if_bot_confirmed_membership(client, db, buyer, monkeypatch):
    from app.services.auth_links import issue_sso_token
    monkeypatch.setattr(settings, 'lab3d_internal_token', 'test-internal-key')
    buyer[0].is_group_member = True
    db.commit()
    token, _ = issue_sso_token(db, user=buyer[0], ttl_minutes=2)
    response = client.post('/auth/internal/sso/verify', json={'token': token},
                           headers={'X-Internal-Token': 'test-internal-key'})
    assert response.status_code == 403
    assert response.json()['reason'] == 'purchase_incomplete'


def test_registration_race_preserves_existing_account(client, db, sales, user_factory, monkeypatch):
    import app.api.auth as auth
    original = auth._upsert_telegram_user
    def appeared_before_upsert(db, **kwargs):
        existing = user_factory(tariff='УВЕРЕННЫЙ', is_group_member=True)
        existing.telegram_chat_id = kwargs['chat_id']
        existing.program_access_from = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.commit()
        return original(db, **kwargs)
    monkeypatch.setattr(auth, '_upsert_telegram_user', appeared_before_upsert)
    response = _oauth(client, monkeypatch)
    assert response.headers['location'] == '/cabinet'
    student = db.query(User).filter_by(telegram_chat_id=999111222).one()
    assert student.is_group_member
    assert student.tariff == 'УВЕРЕННЫЙ'
    assert student.program_access_from.year == 2026
    assert student.program_access_from.month == 1
    assert db.query(PublicEnrollment).count() == 0


def test_catalog_available_with_sales_disabled(client):
    response = client.get('/tariffs')
    assert response.status_code == 200
    assert 'Приём оплат пока закрыт' in response.text


def test_purchase_requires_session(client):
    response = client.get('/purchase', headers={'Accept': 'application/json'})
    assert response.status_code == 401


def test_purchase_start_disabled(client):
    response = client.get('/purchase/start', params={'tariff': 'fake'}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['location'] == '/tariffs'
