"""Paid newcomers get an independent, non-renewable 72-hour Before window."""
from datetime import date, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.payment import KIND_EXTRA, KIND_HOLIDAY, Payment, STATUS_PAID, STATUS_PENDING, STATUS_REFUNDED
from app.models.portfolio_onboarding import PortfolioOnboarding
from app.models.task_block import BLOCK_PORTFOLIO, TaskBlock, TaskBlockState, TaskBlockStudent
from app.services import payments, portfolio_onboarding
from app.services.portfolio_window import snapshot
from app.services.tz import now_msk
from app.services.user_management import tag_annual_newcomer
from tests.test_portfolio_window import _post_before, _task, _work


@pytest.fixture
def newcomer(db, user_factory):
    user = user_factory(portfolio_do_completed=False)
    user.pay_window_start, user.pay_window_end = 10, 15
    tag_annual_newcomer(db, user)
    db.commit()
    return user


def _pay(db, user, *, kind="month", period=None):
    now = now_msk()
    payment = Payment(user_id=user.id, period=period or date(now.year, now.month, 1),
                      kind=kind, amount_kop=10000, tariff=user.tariff, status=STATUS_PENDING)
    db.add(payment)
    db.flush()
    payments.record_paid(db, payment, now=now, source="manual", paid_sum_kop=10000,
                         performed_by_id=user.id)
    db.commit()
    return payment


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


def test_payment_grants_without_starting_clock(db, newcomer):
    payment = _pay(db, newcomer)
    row = db.get(PortfolioOnboarding, newcomer.id)
    assert row.payment_id == payment.id and row.granted_at is not None
    assert row.started_at is None
    state = snapshot(db, user_id=newcomer.id, user_tariff=newcomer.tariff, section="before")
    assert state.any_exists and state.open is None
    assert row.started_at is None


def test_first_opening_is_saved_in_utc_without_sqlite_clock_shift(db, newcomer):
    _pay(db, newcomer)
    row = portfolio_onboarding.start(db, newcomer.id)
    assert abs((row.started_at - now_msk().astimezone(timezone.utc)).total_seconds()) < 2


def _seed(db):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "alembic/versions/a61f0e9c73d2_portfolio_onboarding.py"
    spec = importlib.util.spec_from_file_location("portfolio_onboarding_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.seed_existing(db.connection())
    db.expire_all()


def test_migration_includes_paid_newcomer_with_old_automatic_timer(db, newcomer):
    payment = _pay(db, newcomer)
    db.query(PortfolioOnboarding).delete()
    task = _task(db, newcomer)
    block = TaskBlock(task_id=task.id, block_type=BLOCK_PORTFOLIO, portfolio_window_hours=72)
    db.add(block)
    db.flush()
    db.add(TaskBlockState(block_id=block.id, user_id=newcomer.id,
                          started_at=now_msk().astimezone(timezone.utc) - timedelta(days=9)))
    db.commit()
    _seed(db)
    row = db.get(PortfolioOnboarding, newcomer.id)
    assert row is not None and row.payment_id == payment.id and row.started_at is None
    granted = row.granted_at
    _seed(db)
    assert db.get(PortfolioOnboarding, newcomer.id).granted_at == granted


def test_migration_preserves_explicit_personal_72_hour_window(db, newcomer):
    _pay(db, newcomer)
    db.query(PortfolioOnboarding).delete()
    task = _task(db, newcomer)
    block = TaskBlock(task_id=task.id, block_type=BLOCK_PORTFOLIO,
                      bypass_sequence=True, is_required=False, portfolio_window_hours=72)
    db.add(block)
    db.flush()
    started = now_msk().astimezone(timezone.utc) - timedelta(hours=3)
    state = TaskBlockState(block_id=block.id, user_id=newcomer.id, started_at=started)
    db.add_all([state, TaskBlockStudent(block_id=block.id, user_id=newcomer.id)])
    db.commit()
    _seed(db)
    assert db.get(PortfolioOnboarding, newcomer.id).started_at == started
    assert state.started_at == started


def test_migration_waits_for_payment_and_excludes_existing_work(db, newcomer, user_factory):
    from app.constants import ANNUAL_NEWCOMER_TAG
    from app.services.tags import get_or_create_tag, add_tag_to_user
    db.query(PortfolioOnboarding).delete()
    existing = user_factory(vk_id=100003, portfolio_do_completed=False)
    _work(db, existing)
    add_tag_to_user(db, existing.id, get_or_create_tag(db, ANNUAL_NEWCOMER_TAG).id)
    db.commit()
    _seed(db)
    assert db.get(PortfolioOnboarding, newcomer.id).granted_at is None
    assert db.get(PortfolioOnboarding, existing.id) is None


def test_two_simultaneous_openings_keep_one_start(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.db.database import Base
    from app.models.user import User
    engine = create_engine(f"sqlite:///{(tmp_path / 'opening.db').as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(vk_id=770001, name="Parallel opening", program="annual")
        db.add(user)
        db.flush()
        uid = user.id
        payment = Payment(user_id=uid, period=date(2026, 11, 1), amount_kop=10000,
                          kind="month", status=STATUS_PAID)
        db.add(payment)
        db.flush()
        db.add(PortfolioOnboarding(user_id=uid, payment_id=payment.id,
                                    granted_at=now_msk().astimezone(timezone.utc)))
        db.commit()
    barrier = Barrier(2)
    def open_once():
        with Session(engine) as db:
            row = db.get(PortfolioOnboarding, uid)
            assert row.started_at is None
            barrier.wait(timeout=10)
            return portfolio_onboarding.start(db, uid).started_at
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            times = list(workers.map(lambda _: open_once(), range(2)))
        assert times[0] is not None and times[0] == times[1]
    finally:
        engine.dispose()


def test_paid_upload_satisfies_a_legacy_block_that_opens_later(db, newcomer):
    from app.api.cabinet_tracker import _portfolio_block_done
    from app.services.cycle_feed import build_cycle_feed
    from app.services.tz import today_msk
    _pay(db, newcomer)
    portfolio_onboarding.start(db, newcomer.id)
    _work(db, newcomer, created_at=now_msk().astimezone(timezone.utc))
    task = _task(db, newcomer)
    block = TaskBlock(task_id=task.id, block_type=BLOCK_PORTFOLIO,
                      is_required=True, portfolio_window_hours=72)
    db.add(block)
    db.commit()
    assert _portfolio_block_done(db, block, newcomer.id)
    feed = build_cycle_feed(db, user_id=newcomer.id, user_tariff=newcomer.tariff,
                            start=today_msk(), end=today_msk())
    assert next(s for s in feed if s.get("block") and s["block"].id == block.id)["status"] == "done"
def test_newcomer_registration_without_payment_cannot_use_legacy_fallback(db, newcomer, client, session_factory):
    _login(client, session_factory, newcomer)
    page = client.get("/upload?section=before")
    assert 'id="upload-form"' not in page.text
    assert db.get(PortfolioOnboarding, newcomer.id).started_at is None
    assert _post_before(client).status_code == 422


@pytest.mark.parametrize("kind", [KIND_EXTRA, KIND_HOLIDAY])
def test_non_annual_payment_does_not_grant(db, newcomer, kind):
    _pay(db, newcomer, kind=kind)
    assert db.get(PortfolioOnboarding, newcomer.id).payment_id is None


def test_renewal_of_existing_student_does_not_create_onboarding(db, user_factory):
    old = user_factory(portfolio_do_completed=False)
    _pay(db, old)
    assert db.get(PortfolioOnboarding, old.id) is None


def test_existing_before_work_does_not_get_new_window(db, user_factory):
    user = user_factory(portfolio_do_completed=False)
    _work(db, user)
    tag_annual_newcomer(db, user)
    _pay(db, user)
    assert db.get(PortfolioOnboarding, user.id) is None


def test_dashboard_reads_do_not_start_and_first_upload_get_does(db, newcomer, client, session_factory):
    _pay(db, newcomer)
    _login(client, session_factory, newcomer)
    page = client.get("/cabinet/learning")
    assert page.status_code == 200 and 'data-role="portfolio-onboarding"' in page.text
    assert db.get(PortfolioOnboarding, newcomer.id).started_at is None
    page = client.get("/upload?section=before")
    assert page.status_code == 200 and 'id="upload-form"' in page.text
    row = db.get(PortfolioOnboarding, newcomer.id)
    db.refresh(row)
    started = row.started_at
    assert started is not None
    state = snapshot(db, user_id=newcomer.id, user_tariff=newcomer.tariff, section="before")
    assert state.open.closes_at - state.open.opens_at == timedelta(hours=72)
    client.get("/upload?section=before&block=999999")
    db.refresh(row)
    assert row.started_at == started


def test_upload_post_cannot_start_clock(db, newcomer, client, session_factory):
    _pay(db, newcomer)
    _login(client, session_factory, newcomer)
    assert _post_before(client).status_code == 422
    assert db.get(PortfolioOnboarding, newcomer.id).started_at is None


def test_after_get_never_starts_before_window(db, newcomer, client, session_factory):
    _pay(db, newcomer)
    _login(client, session_factory, newcomer)
    client.get("/upload?section=after")
    assert db.get(PortfolioOnboarding, newcomer.id).started_at is None


def test_expired_window_cannot_be_bypassed_by_weekly_window_or_block_id(db, newcomer, client, session_factory):
    _pay(db, newcomer)
    row = db.get(PortfolioOnboarding, newcomer.id)
    row.started_at = now_msk() - timedelta(hours=73)
    task = _task(db, newcomer)
    block = TaskBlock(task_id=task.id, block_type=BLOCK_PORTFOLIO, bypass_sequence=True)
    db.add(block)
    db.commit()
    _login(client, session_factory, newcomer)
    assert 'id="upload-form"' not in client.get(f"/upload?section=before&block={block.id}").text
    assert _post_before(client).status_code == 422
    assert _post_before(client, section="after", month="сентябрь").status_code == 200


def test_exact_deadline_is_closed_and_refresh_does_not_extend(db, newcomer):
    _pay(db, newcomer)
    row = portfolio_onboarding.start(db, newcomer.id)
    started = row.started_at
    deadline = started + timedelta(hours=72)
    with patch("app.services.portfolio_onboarding.now_msk", return_value=deadline), \
            patch("app.services.portfolio_window.now_msk", return_value=deadline):
        state = snapshot(db, user_id=newcomer.id, user_tariff=newcomer.tariff, section="before")
        assert state.open is None and state.last_closed is not None
        assert portfolio_onboarding.start(db, newcomer.id).started_at == started


def test_refund_revokes_access_and_repayment_never_resets_clock(db, newcomer):
    first = _pay(db, newcomer)
    started = portfolio_onboarding.start(db, newcomer.id).started_at
    payments.revert_paid(db, first, refund=True, performed_by_id=newcomer.id, now=now_msk())
    db.commit()
    assert snapshot(db, user_id=newcomer.id, user_tariff=newcomer.tariff, section="before").open is None
    _pay(db, newcomer, period=date(2027, 1, 1))
    row = portfolio_onboarding.start(db, newcomer.id)
    assert row.started_at == started
    assert snapshot(db, user_id=newcomer.id, user_tariff=newcomer.tariff, section="before").open is not None


def test_active_paid_month_keeps_window_when_another_payment_refunded(db, newcomer):
    first = _pay(db, newcomer)
    started = portfolio_onboarding.start(db, newcomer.id).started_at
    _pay(db, newcomer, period=date(2027, 1, 1))
    payments.revert_paid(db, first, refund=True, performed_by_id=newcomer.id, now=now_msk())
    db.commit()
    state = snapshot(db, user_id=newcomer.id, user_tariff=newcomer.tariff, section="before")
    assert state.open is not None and state.open.opens_at == started


def test_first_photo_keeps_entry_and_delete_is_limited_to_window(db, newcomer, client, session_factory):
    _pay(db, newcomer)
    _login(client, session_factory, newcomer)
    client.get("/upload?section=before")
    assert _post_before(client).status_code == 200
    db.refresh(newcomer)
    assert newcomer.portfolio_do_completed
    assert 'data-role="portfolio-onboarding"' in client.get("/cabinet/learning").text
    assert '/upload?section=before' in client.get("/cabinet/portfolio").text
    work = _work(db, newcomer)
    assert client.delete(f"/upload/works/{work.id}").status_code == 200
    row = db.get(PortfolioOnboarding, newcomer.id)
    row.started_at = now_msk() - timedelta(hours=73)
    db.commit()
    work = _work(db, newcomer)
    assert client.delete(f"/upload/works/{work.id}").status_code == 422
    db.refresh(newcomer)
    assert newcomer.portfolio_do_completed


def test_annual_conversion_grants_without_replacing_completed_portfolio(db, user_factory):
    user = user_factory(portfolio_do_completed=False)
    user.program = "holiday"
    user.pay_window_start, user.pay_window_end = 10, 15
    db.commit()
    _pay(db, user)
    assert db.get(PortfolioOnboarding, user.id).payment_id is not None


def test_public_first_payment_creates_candidate_before_activation(db, user_factory):
    from app.models.public_enrollment import PublicEnrollment
    user = user_factory(portfolio_do_completed=False, profile_completed=False)
    user.pay_window_start, user.pay_window_end = 10, 15
    db.add(PublicEnrollment(user_id=user.id, selected_tariff=user.tariff))
    db.commit()
    payment = _pay(db, user)
    row = db.get(PortfolioOnboarding, user.id)
    assert row.payment_id == payment.id and row.started_at is None


def test_another_student_cannot_start_someone_elses_window(db, newcomer, client, session_factory, user_factory):
    _pay(db, newcomer)
    other = user_factory(vk_id=100002)
    _login(client, session_factory, other)
    client.get(f"/upload?section=before&user_id={newcomer.id}")
    assert db.get(PortfolioOnboarding, newcomer.id).started_at is None
