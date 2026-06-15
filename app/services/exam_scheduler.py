"""
Планировщик уведомлений о предстоящих пробниках.

Каждый час проверяет ExamTicket-ы, у которых:
  - start_date наступит в течение 3 дней (или уже сегодня)
  - В ExamTicketAssignee.notified_at IS NULL

Для каждого такого ученика создаёт in-app Notification и проставляет notified_at.
"""
import logging
from datetime import datetime, timezone, timedelta, date

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.database import SessionLocal
from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketAssignee
from app.models.login_token import LoginToken
from app.models.mock_exam_attempt import MockExamAttempt
from app.models.notification import Notification
from app.models.role import Role
from app.models.session import Session
from app.models.user import User
from app.services.tz import today_msk

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

NOTIFY_DAYS_BEFORE = 3  # за сколько дней до начала отправляем уведомление


def _run_notification_check() -> None:
    """Запускается по расписанию. Создаёт уведомления для ближайших пробников."""
    db = SessionLocal()
    try:
        today = today_msk()
        notify_threshold = today + timedelta(days=NOTIFY_DAYS_BEFORE)

        # Билеты, у которых начало сдачи через 3 дня или раньше, задание опубликовано
        tickets = (
            db.query(ExamTicket)
            .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
            .filter(
                ExamAssignment.status == "published",
                ExamTicket.start_date <= notify_threshold,
                ExamTicket.end_date >= today,   # ещё не закончился
            )
            .all()
        )

        if not tickets:
            return

        ticket_ids = [t.id for t in tickets]
        ticket_map = {t.id: t for t in tickets}

        # Для каждого билета найдём тех, кому не отправлено уведомление
        pending = (
            db.query(ExamTicketAssignee)
            .filter(
                ExamTicketAssignee.ticket_id.in_(ticket_ids),
                ExamTicketAssignee.notified_at.is_(None),
            )
            .all()
        )

        # Для билетов с assign_to_all=True — найти всех активных учеников
        all_tickets_ids = [t.id for t in tickets if t.assign_to_all]
        if all_tickets_ids:
            # Получаем ID всех активных учеников (rank=1)
            student_role = db.query(Role).filter(Role.rank == 1).first()
            if student_role:
                all_students = (
                    db.query(User.id)
                    .filter(User.role_id == student_role.id, User.is_active == True)
                    .all()
                )
                all_student_ids = {row.id for row in all_students}

                # Для каждого "всем" билета убеждаемся что все ученики в assignees
                for ticket_id in all_tickets_ids:
                    existing_ids = {
                        a.user_id for a in pending if a.ticket_id == ticket_id
                    }
                    # Находим уже существующих assignees
                    existing_all = (
                        db.query(ExamTicketAssignee.user_id)
                        .filter(ExamTicketAssignee.ticket_id == ticket_id)
                        .all()
                    )
                    existing_assigned = {row.user_id for row in existing_all}
                    missing = all_student_ids - existing_assigned
                    if missing:
                        stmt = pg_insert(ExamTicketAssignee).values(
                            [{"ticket_id": ticket_id, "user_id": uid} for uid in missing]
                        ).on_conflict_do_nothing(index_elements=["ticket_id", "user_id"])
                        db.execute(stmt)
                        db.flush()
                        # reload pending для только что добавленных
                        new_assignees = (
                            db.query(ExamTicketAssignee)
                            .filter(
                                ExamTicketAssignee.ticket_id == ticket_id,
                                ExamTicketAssignee.user_id.in_(missing),
                                ExamTicketAssignee.notified_at.is_(None),
                            )
                            .all()
                        )
                        pending.extend(new_assignees)

        # Отправляем уведомления
        now = datetime.now(timezone.utc)
        sent = 0
        for assignee in pending:
            if assignee.notified_at is not None:
                continue
            ticket = ticket_map.get(assignee.ticket_id)
            if not ticket:
                continue
            assignment = db.query(ExamAssignment).filter(
                ExamAssignment.id == ticket.assignment_id
            ).first()
            if not assignment:
                continue

            days_left = (ticket.start_date - today).days
            if days_left > NOTIFY_DAYS_BEFORE:
                continue

            if days_left <= 0:
                when_text = "сегодня"
            elif days_left == 1:
                when_text = "завтра"
            else:
                when_text = f"через {days_left} дн."

            notif = Notification(
                user_id=assignee.user_id,
                title=f"Пробник по {assignment.subject} — {when_text}",
                text=(
                    f"Билет {ticket.ticket_number}: {ticket.title}\n"
                    f"Период сдачи: {ticket.start_date.strftime('%d.%m.%Y')} — "
                    f"{ticket.end_date.strftime('%d.%m.%Y')}"
                ),
            )
            db.add(notif)
            assignee.notified_at = now
            sent += 1

        db.commit()
        if sent:
            logger.info("Exam scheduler: отправлено %d уведомлений о пробниках", sent)

    except Exception:
        logger.exception("Ошибка в планировщике уведомлений о пробниках")
        db.rollback()
    finally:
        db.close()


def _run_mock_exam_expiry_check() -> None:
    """Каждые несколько минут помечает expired_at у открытых MockExamAttempt,
    чей билет вышел из периода сдачи (end_date < today) или чьё задание стало
    неопубликованным/архивным (status != "published").

    Без этого «зависшая» попытка (ученик не сдал работу до конца периода
    билета) осталась бы completed_at IS NULL навечно: mock_exam_start
    резюмировал бы её со снимком архивного билета, а progress-check слал бы
    уведомления о мёртвой попытке.
    """
    db = SessionLocal()
    try:
        today = today_msk()
        now = datetime.now(timezone.utc)

        expired = (
            db.query(MockExamAttempt)
            .join(ExamTicket, MockExamAttempt.ticket_id == ExamTicket.id)
            .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
            .filter(
                MockExamAttempt.completed_at.is_(None),
                MockExamAttempt.expired_at.is_(None),
                or_(
                    ExamTicket.end_date < today,
                    ExamAssignment.status != "published",
                ),
            )
            .all()
        )
        for attempt in expired:
            attempt.expired_at = now

        if expired:
            db.commit()
            logger.info("Mock-exam expiry: помечено %d истёкших попыток", len(expired))

    except Exception:
        logger.exception("Ошибка в mock-exam expiry check")
        db.rollback()
    finally:
        db.close()


def _run_mock_exam_progress_check() -> None:
    """Каждую минуту проверяет активные MockExamAttempt и отправляет уведомления
    о прогрессе: 2ч прошло, 3ч прошло, 10 мин до окончания. Флаги защищают от
    дубликатов.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        TWO_H = timedelta(hours=2)
        THREE_H = timedelta(hours=3)
        FIFTY_M = timedelta(minutes=50)  # 4ч - 10 мин = 3ч50м → точка «осталось 10 мин»

        active = (
            db.query(MockExamAttempt)
            .filter(
                MockExamAttempt.completed_at.is_(None),
                MockExamAttempt.expired_at.is_(None),
            )
            .all()
        )

        sent = 0
        for a in active:
            elapsed = now - a.started_at

            if elapsed >= TWO_H and not a.notif_2h_sent:
                db.add(Notification(
                    user_id=a.user_id,
                    title=f"Пробник «{a.subject}»: прошло 2 часа",
                    text="Осталось 2 часа. Продолжайте работу.",
                ))
                a.notif_2h_sent = True
                sent += 1

            if elapsed >= THREE_H and not a.notif_3h_sent:
                db.add(Notification(
                    user_id=a.user_id,
                    title=f"Пробник «{a.subject}»: остался 1 час",
                    text="Остался 1 час до окончания времени.",
                ))
                a.notif_3h_sent = True
                sent += 1

            if elapsed >= FIFTY_M + timedelta(hours=3) and not a.notif_10min_sent:
                # 3ч + 50м = 3ч50м elapsed → осталось 10 мин
                db.add(Notification(
                    user_id=a.user_id,
                    title=f"Пробник «{a.subject}»: осталось 10 минут",
                    text="До окончания времени — 10 минут. Загрузите фото работы.",
                ))
                a.notif_10min_sent = True
                sent += 1

        db.commit()
        if sent:
            logger.info("Mock-exam progress: отправлено %d уведомлений", sent)

    except Exception:
        logger.exception("Ошибка в mock-exam progress check")
        db.rollback()
    finally:
        db.close()


def _run_cleanup() -> None:
    """Каждые 6 часов удаляет протухшие сессии и login-токены."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        sessions_deleted = (
            db.query(Session)
            .filter(Session.expires_at < now)
            .delete(synchronize_session=False)
        )
        tokens_deleted = (
            db.query(LoginToken)
            .filter(LoginToken.expires_at < now)
            .delete(synchronize_session=False)
        )
        db.commit()
        if sessions_deleted or tokens_deleted:
            logger.info(
                "Cleanup: удалено %d сессий, %d токенов",
                sessions_deleted,
                tokens_deleted,
            )
    except Exception:
        logger.exception("Ошибка в cleanup job")
        db.rollback()
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _run_notification_check,
        trigger="interval",
        hours=1,
        next_run_time=datetime.now(timezone.utc),  # запустить сразу при старте
        id="exam_notifications",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _run_mock_exam_progress_check,
        trigger="interval",
        minutes=1,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        id="mock_exam_progress",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    _scheduler.add_job(
        _run_mock_exam_expiry_check,
        trigger="interval",
        minutes=5,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=15),
        id="mock_exam_expiry",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _run_cleanup,
        trigger="interval",
        hours=6,
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5),
        id="session_cleanup",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )
    _scheduler.start()
    logger.info(
        "Exam scheduler started (exam_notifications=1h, mock_exam_progress=1min, "
        "mock_exam_expiry=5min, cleanup=6h)"
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Exam scheduler stopped")
