"""Tests for hard_delete_user (кнопка «Удалить навсегда» в кабинете суперадмина).

Таблицы с `ondelete=CASCADE`/`SET NULL` на уровне БД (feedback_messages,
feedback_photos, mock_exam_attempts, вся ветка homework_submissions,
tracker/video-таблицы и т.д.) здесь не проверяются: тестовый движок —
SQLite без `PRAGMA foreign_keys=ON`, и такой каскад в нём не сработает
независимо от того, верен код или нет (сама СУБД его не применяет). Эти FK
проверены чтением моделей (см. комментарии в user_management.py). Тесты ниже
покрывают ровно то, что делает сама функция явными DELETE/UPDATE.
"""
from datetime import date, datetime, timedelta, timezone

from app.models.audit_log import AuditLog
from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage
from app.models.legacy_portfolio_photo import LegacyPortfolioPhoto
from app.models.login_token import LoginToken
from app.models.mock_exam_lock import MockExamLock
from app.models.notification import Notification
from app.models.session import Session as DbSession
from app.models.tag import Tag, UserTag
from app.models.telegram_link_token import TelegramLinkToken
from app.models.upload_log import UploadLog
from app.models.user import User
from app.models.work import Work
from app.services.user_management import hard_delete_user


def _make_work(db, student, **overrides):
    work = Work(
        user_id=student.id,
        work_type="before",
        month="сентябрь",
        year=2026,
        filename="photo.jpg",
        **overrides,
    )
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


def test_hard_delete_removes_student_and_all_owned_traces(db, user_factory, session_factory):
    superadmin = user_factory(vk_id=920001, name="Superadmin", role_name="суперадмин", is_admin=True)
    curator = user_factory(vk_id=920002, name="Curator", role_name="куратор")
    student = user_factory(vk_id=920003, name="Ветвь Александрия", role_name="ученик")
    student.telegram_chat_id = 777001
    student.tg_username = "test_student"
    db.commit()

    work = _make_work(db, student)
    feedback = Feedback(work_id=work.id, curator_id=curator.id)
    db.add(feedback)
    db.commit()
    db.add(FeedbackMessage(
        feedback_id=feedback.id, sender_id=student.id, sender_role="student", text="Привет!",
    ))
    db.add(Notification(user_id=student.id, title="Работа проверена"))
    db.add(ExamCycle(user_id=student.id, subject="Рисунок", started_at=date(2026, 9, 1)))
    db.add(MockExamLock(user_id=student.id, subject="Рисунок"))
    tag = Tag(name="test-tag")
    db.add(tag)
    db.commit()
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.add(LoginToken(
        user_id=student.id, token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db.add(TelegramLinkToken(
        user_id=student.id, token_hash="b" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db.add(UploadLog(
        user_id=student.id, student_name=student.name, tariff="УВЕРЕННЫЙ",
        month="сентябрь", photo_type="before",
    ))
    db.add(LegacyPortfolioPhoto(
        user_id=student.id, dialog_id="d1", month="сентябрь", year=2026,
        original_filename="old.jpg", s3_path="legacy/old.jpg", s3_url="https://s3/old.jpg",
        sent_at=datetime.now(timezone.utc),
    ))
    session_factory(student)
    # Собственный след: ученик сам сменил тариф — performed_by_id == target_user_id,
    # ровно та строка, что не давала бы `audit_logs.performed_by_id` (NOT NULL) уйти.
    db.add(AuditLog(action="tariff_change", performed_by_id=student.id, target_user_id=student.id,
                     details="tariff: A -> B"))
    # След работы персонала над ней.
    db.add(AuditLog(action="user_block", performed_by_id=superadmin.id, target_user_id=student.id,
                     details="Заблокирован"))
    db.commit()

    student_id = student.id

    ok, error = hard_delete_user(db, target_user_id=student_id, performed_by_id=superadmin.id)

    assert ok is True
    assert error is None
    assert db.query(User).filter(User.id == student_id).first() is None
    assert db.query(Work).filter(Work.user_id == student_id).count() == 0
    assert db.query(Feedback).filter(Feedback.id == feedback.id).count() == 0
    # feedback_messages уходит DB-каскадом от feedbacks.id (ondelete=CASCADE) —
    # см. docstring модуля, SQLite-тест его не проверяет.
    assert db.query(Notification).filter(Notification.user_id == student_id).count() == 0
    assert db.query(ExamCycle).filter(ExamCycle.user_id == student_id).count() == 0
    assert db.query(MockExamLock).filter(MockExamLock.user_id == student_id).count() == 0
    assert db.query(UserTag).filter(UserTag.user_id == student_id).count() == 0
    assert db.query(LoginToken).filter(LoginToken.user_id == student_id).count() == 0
    assert db.query(TelegramLinkToken).filter(TelegramLinkToken.user_id == student_id).count() == 0
    assert db.query(UploadLog).filter(UploadLog.user_id == student_id).count() == 0
    assert db.query(LegacyPortfolioPhoto).filter(LegacyPortfolioPhoto.user_id == student_id).count() == 0
    assert db.query(DbSession).filter(DbSession.user_id == student_id).count() == 0

    # Ни один audit_log больше не ссылается на удалённого ни как на цель, ни
    # как на актора — только новая финальная запись с target_user_id=NULL.
    remaining = db.query(AuditLog).all()
    assert len(remaining) == 1
    assert remaining[0].action == "user_hard_delete"
    assert remaining[0].target_user_id is None
    assert remaining[0].performed_by_id == superadmin.id
    assert "Ветвь Александрия" in remaining[0].details


def test_hard_delete_refuses_non_student(db, user_factory):
    superadmin = user_factory(vk_id=920010, name="Superadmin", role_name="суперадмин", is_admin=True)
    curator = user_factory(vk_id=920011, name="Curator", role_name="куратор")

    ok, error = hard_delete_user(db, target_user_id=curator.id, performed_by_id=superadmin.id)

    assert ok is False
    assert error is not None
    assert db.query(User).filter(User.id == curator.id).first() is not None


def test_hard_delete_refuses_non_superadmin_actor(db, user_factory):
    admin = user_factory(vk_id=920020, name="Admin", role_name="админ", is_admin=True)
    student = user_factory(vk_id=920021, name="Student", role_name="ученик")

    ok, error = hard_delete_user(db, target_user_id=student.id, performed_by_id=admin.id)

    assert ok is False
    assert db.query(User).filter(User.id == student.id).first() is not None


def test_hard_delete_aborts_on_foreign_data_reference(db, user_factory):
    """Данные, где удаляемый — куратор ЧУЖОГО диалога (аномалия, для роли
    «ученик» в норме невозможная), должны остановить удаление целиком, а не
    утащить за собой чужую переписку."""
    superadmin = user_factory(vk_id=920030, name="Superadmin", role_name="суперадмин", is_admin=True)
    student = user_factory(vk_id=920031, name="Student", role_name="ученик")
    other_student = user_factory(vk_id=920032, name="Other Student", role_name="ученик")

    other_work = _make_work(db, other_student)
    foreign_feedback = Feedback(work_id=other_work.id, curator_id=student.id)
    db.add(foreign_feedback)
    db.commit()

    ok, error = hard_delete_user(db, target_user_id=student.id, performed_by_id=superadmin.id)

    assert ok is False
    assert "curator_id" in error
    assert db.query(User).filter(User.id == student.id).first() is not None
    assert db.query(Feedback).filter(Feedback.id == foreign_feedback.id).count() == 1


def test_hard_delete_lets_same_telegram_account_start_fresh(db, user_factory):
    """Свойство, ради которого кнопка вообще существует: тот же chat_id
    после полного удаления заводит НОВЫЙ профиль, а не воскрешает старый."""
    from app.api.auth import _upsert_telegram_user

    superadmin = user_factory(vk_id=920040, name="Superadmin", role_name="суперадмин", is_admin=True)
    student = user_factory(vk_id=920041, name="Ветвь Александрия", role_name="ученик")
    student.telegram_chat_id = 777099
    student.curator_id = None
    db.commit()
    old_id = student.id

    ok, _ = hard_delete_user(db, target_user_id=old_id, performed_by_id=superadmin.id)
    assert ok is True

    new_user, created = _upsert_telegram_user(db, chat_id=777099, tg_from=None, is_group_member=False)
    db.commit()

    # `created=True` — сигнал «завели новую строку», не зависящий от СУБД:
    # id после удаления в SQLite может переиспользоваться (в отличие от
    # Postgres SERIAL на проде), сравнивать id тут не годится.
    assert created is True
    assert new_user.profile_completed is False
    assert new_user.curator_id is None
    assert new_user.portfolio_do_completed is False
