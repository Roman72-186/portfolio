"""Рассылки ученикам от преподавателя через бота (владелец 07.10.2026, просьба
службы заботы: «от Лизы кружочки / голосовые / посты… как будто бы Лиза с ними
лично общается»).

Рассылка — одно сообщение: текст в разметке Telegram и не больше одного
вложения (фото, голосовое или кружок). Кому — та же тройка, что у блока
задания: тарифы, уровень точки А, ученики поимённо, правило одно —
`task_blocks.is_block_open_to` (`services/broadcasts.py`).

Списки — нормализованные таблицы, как у `TaskBlockTariff`/`Level`/`Student`:
JSON-полей в проекте нет.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

STATUS_DRAFT = "draft"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"

MEDIA_PHOTO = "photo"
MEDIA_VOICE = "voice"
MEDIA_VIDEO_NOTE = "video_note"
MEDIA_KINDS = (MEDIA_PHOTO, MEDIA_VOICE, MEDIA_VIDEO_NOTE)

# Строка журнала по ученику. «Ждёт» — единственный статус, который отправка
# берёт в работу: прерванную рассылку можно дослать без повторов.
RECIPIENT_PENDING = "pending"
RECIPIENT_SENT = "sent"
RECIPIENT_FAILED = "failed"
RECIPIENT_NO_TELEGRAM = "no_telegram"
RECIPIENT_NOTIFICATIONS_OFF = "notifications_off"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Разметка Telegram (HTML-подмножество Bot API), уже очищенная
    # `broadcasts.clean_telegram_html`: в базе не лежит ничего, что Telegram
    # не примет.
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    media_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    media_s3_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_s3_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_filename: Mapped[str | None] = mapped_column(String(200), nullable=True)
    media_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Файл уже лежит у Telegram: получен на проверке у преподавателя, ученикам
    # уходит по нему без повторной загрузки с нашего сервера.
    telegram_file_id: Mapped[str | None] = mapped_column(String(300), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_DRAFT)

    # Проверка у преподавателя: что именно было показано (отпечаток текста,
    # вложения и выбора получателей) и куда. Отправить можно только ту версию,
    # которую видели: любая правка меняет отпечаток.
    preview_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preview_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    preview_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preview_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sent_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Момент, когда отправку взяли в работу. По нему «Дослать» понимает, что
    # прежний проход умер (перезапуск приложения), а не идёт прямо сейчас.
    sending_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'sending', 'sent')", name="ck_broadcasts_status",
        ),
        CheckConstraint(
            "media_kind IS NULL OR media_kind IN ('photo', 'voice', 'video_note')",
            name="ck_broadcasts_media_kind",
        ),
        Index("ix_broadcasts_created_at", "created_at"),
    )


class BroadcastTariff(Base):
    """Тариф получателей. Значения — `app.constants.TARIFFS`, проверка в
    сервисе, как у `TaskBlockTariff`."""

    __tablename__ = "broadcast_tariffs"

    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("broadcasts.id", ondelete="CASCADE"), primary_key=True
    )
    tariff: Mapped[str] = mapped_column(String(50), primary_key=True)


class BroadcastLevel(Base):
    """Уровень точки А получателей (1 или 2)."""

    __tablename__ = "broadcast_levels"

    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("broadcasts.id", ondelete="CASCADE"), primary_key=True
    )
    level: Mapped[int] = mapped_column(Integer, primary_key=True)

    __table_args__ = (
        CheckConstraint("level IN (1, 2)", name="ck_broadcast_levels_level"),
    )


class BroadcastStudent(Base):
    """Ученик, выбранный поимённо: получает при любом тарифе и уровне."""

    __tablename__ = "broadcast_students"

    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("broadcasts.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )


class BroadcastRecipient(Base):
    """Журнал отправки: одна строка на ученика, заводится в момент нажатия
    «Отправить» — список получателей с этой секунды не меняется."""

    __tablename__ = "broadcast_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RECIPIENT_PENDING)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_recipients_user"),
        Index("ix_broadcast_recipients_status", "broadcast_id", "status"),
    )
