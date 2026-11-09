"""A single 72-hour Before window after a newcomer's confirmed annual payment."""
from datetime import timedelta, timezone

from sqlalchemy import exists, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import PROGRAM_ANNUAL
from app.models.payment import KIND_MONTH, Payment, STATUS_PAID
from app.models.portfolio_onboarding import PortfolioOnboarding
from app.models.user import User
from app.models.work import Work, WORK_TYPE_BEFORE
from app.services.tz import now_msk

WINDOW_HOURS = 72


def _utc(moment):
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def ensure_candidate(db: Session, user: User) -> PortfolioOnboarding | None:
    """Called only at new annual enrollment, never at a monthly renewal."""
    row = db.get(PortfolioOnboarding, user.id)
    if row is not None:
        return row
    if user.program != PROGRAM_ANNUAL or user.portfolio_do_completed:
        return None
    if db.query(Work.id).filter(
        Work.user_id == user.id, Work.work_type == WORK_TYPE_BEFORE, Work.status == "success",
    ).first():
        return None
    try:
        # Another payment/login may create the same candidate concurrently.
        # A savepoint protects the enclosing payment transaction.
        with db.begin_nested():
            row = PortfolioOnboarding(user_id=user.id)
            db.add(row)
            db.flush()
    except IntegrityError:
        row = db.get(PortfolioOnboarding, user.id)
        if row is None:
            raise
    return row


def grant_after_payment(db: Session, payment: Payment, *, new_enrollment: bool = False) -> None:
    if payment.kind != KIND_MONTH or payment.status != STATUS_PAID:
        return
    user = db.get(User, payment.user_id)
    if user is None or user.program != PROGRAM_ANNUAL:
        return
    if new_enrollment:
        ensure_candidate(db, user)
    # Candidates are persisted by the existing new-enrollment path. An old
    # student's first recorded online payment alone is not a new enrollment.
    row = db.get(PortfolioOnboarding, user.id)
    if row is not None and row.granted_at is None:
        db.execute(update(PortfolioOnboarding).where(
            PortfolioOnboarding.user_id == user.id,
            PortfolioOnboarding.granted_at.is_(None),
        ).values(payment_id=payment.id, granted_at=now_msk().astimezone(timezone.utc)))
        db.expire(row)


def payment_active(db: Session, user_id: int) -> bool:
    # A refund must not close access supported by another paid annual month.
    return db.query(Payment.id).filter(
        Payment.user_id == user_id, Payment.kind == KIND_MONTH, Payment.status == STATUS_PAID,
    ).first() is not None


def start(db: Session, user_id: int) -> PortfolioOnboarding | None:
    """Only the authenticated Before upload GET calls this function.

    The conditional UPDATE elects one opening time across concurrent requests;
    reading dashboards, POSTing files or paying again cannot move that time.
    """
    row = db.get(PortfolioOnboarding, user_id)
    if row is None or row.granted_at is None or row.started_at is not None:
        return row
    paid = exists().where(
        Payment.user_id == user_id, Payment.kind == KIND_MONTH, Payment.status == STATUS_PAID,
    )
    result = db.execute(update(PortfolioOnboarding).where(
        PortfolioOnboarding.user_id == user_id,
        PortfolioOnboarding.granted_at.is_not(None),
        PortfolioOnboarding.started_at.is_(None),
        paid,
    ).values(started_at=now_msk().astimezone(timezone.utc)))
    if result.rowcount:
        db.commit()
    db.refresh(row)
    return row


def window(db: Session, user_id: int):
    """None means legacy rules; an unstarted managed window is always closed."""
    row = db.get(PortfolioOnboarding, user_id)
    if row is None:
        return None
    # Local import avoids a cycle: the shared resolver owns the public type.
    from app.services.portfolio_window import PortfolioWindow
    started = _utc(row.started_at) if row.started_at else None
    deadline = started + timedelta(hours=WINDOW_HOURS) if started else None
    return PortfolioWindow(
        block_id=None, task_id=None, section=WORK_TYPE_BEFORE,
        opens_at=started, closes_at=deadline,
        is_open=bool(row.granted_at and started and deadline > now_msk() and payment_active(db, user_id)),
    )


def uploaded(db: Session, user_id: int) -> bool | None:
    """A later teaching block must accept the same initial Before upload.

    None preserves legacy block-specific completion. A managed admission uses
    its single intentional opening time even if a teaching block opens later.
    """
    row = db.get(PortfolioOnboarding, user_id)
    if row is None:
        return None
    if row.started_at is None:
        return False
    return db.query(Work.id).filter(
        Work.user_id == user_id, Work.work_type == WORK_TYPE_BEFORE,
        Work.status == "success", Work.created_at >= _utc(row.started_at),
    ).first() is not None


def card(db: Session, user_id: int) -> dict | None:
    """Display-only state. Rendering a card must never start a timer."""
    row = db.get(PortfolioOnboarding, user_id)
    if row is None or row.granted_at is None or not payment_active(db, user_id):
        return None
    from app.services.portfolio_window import format_deadline_msk
    state = window(db, user_id)
    return {
        "started": row.started_at is not None,
        "expired": bool(row.started_at and not state.is_open),
        "deadline": format_deadline_msk(state.closes_at),
    }
