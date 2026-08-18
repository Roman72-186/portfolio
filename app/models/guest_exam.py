"""Гостевой режим — ВРЕМЕННЫЙ модуль: пробник для участников без регистрации.

Билеты — настоящие `ExamTicket`/`ExamAssignment` (kind="guest", см. app/constants.py),
переиспользуют ту же таблицу, что и билеты реального пробника, чтобы не дублировать
модель. Изолированы от реальных учеников только фильтром `kind != "guest"` во всех
местах, которые резолвят билеты для User (см. комментарий у ASSIGNMENT_KINDS).

Участник, вход, сдача — свои таблицы ниже. Намеренно не связаны с
User.vk_id/Session/ExamCycle/Feedback/MockExamAttempt/Work — см. обоснование в
plans/2026-08-18-apparchi-student-cabinet-and-guest-trial.md, трек B: гость не
заводится как настоящий User, а оценка гостевой работы не пишется в Work, чтобы
не протечь в дашборд/статистику реальных учеников (Work.user_id NOT NULL и не
везде джойнится на User).

Ссылка бессрочная (владелец включает/выключает вручную через `is_active`, без
окна дат) — одна ссылка рассылается всем участникам, входов сколько угодно.
Каждый вход в `GuestVisit` — минимальная статистика посещений.

Снести после того, как результаты экспортированы (scripts/export_guest_exam_
results.py) и владелец подтвердил, что данные больше не нужны — отдельной
alembic-миграцией, не автоматически.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Integer, String, Boolean, DateTime, Text, Numeric,
    ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class GuestExamConfig(Base):
    """Сама бессрочная ссылка: единственный переключатель — `is_active`."""

    __tablename__ = "guest_exam_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class GuestVisit(Base):
    """Лог входов по ссылке — минимальная статистика («сколько раз заходили»).
    Пишется на каждый заход на лендинг, `participant_id` заполняется, если гость
    уже узнан по cookie/коду."""

    __tablename__ = "guest_visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guest_exam_configs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    participant_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("guest_participants.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_guest_visits_config", "config_id", "created_at"),
    )


class GuestParticipant(Base):
    """Гость. От него собирается только имя — никакого телефона/telegram/email."""

    __tablename__ = "guest_participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guest_exam_configs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Короткий код для повторного входа с другого устройства/браузера — единственная
    # «учётная запись» гостя, алфавит без спутываемых символов (см. guest_exam.py).
    participant_code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class GuestSubmission(Base):
    """Единица «билет → работа → балл» на пару (участник, предмет). Без диалога
    проверки и без итераций — куратор один раз ставит балл и комментарий."""

    __tablename__ = "guest_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    participant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guest_participants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject: Mapped[str] = mapped_column(String(50), nullable=False)

    # Снимок билета — правка/удаление ExamTicket не портит уже выданную попытку
    # (тот же паттерн, что MockExamAttempt для ExamTicket у реальных учеников).
    ticket_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("exam_tickets.id", ondelete="SET NULL"), nullable=True
    )
    ticket_title: Mapped[str] = mapped_column(String(200), nullable=False)
    ticket_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    s3_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    s3_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Необязательная альтернатива/дополнение к текстовому комментарию — фото
    # обратной связи (например, разметка поверх присланной работы).
    feedback_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    feedback_image_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    scored_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="issued", nullable=False)
    # issued | submitted | scored

    __table_args__ = (
        UniqueConstraint(
            "participant_id", "subject", name="uq_guest_submission_participant_subject"
        ),
        Index("ix_guest_submissions_status", "status"),
    )
