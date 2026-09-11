from datetime import datetime, timezone

from sqlalchemy import Integer, BigInteger, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.crypto import EncryptedString
from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vk_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Аватар, загруженный самим учеником один раз в кабинете (не путать с photo_url —
    # тот приходит из Telegram и перезаписывается при каждом входе, см. auth.py).
    custom_avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tariff: Mapped[str] = mapped_column(String(50), default="УВЕРЕННЫЙ")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_group_member: Mapped[bool] = mapped_column(Boolean, default=False)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    parent_phone: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    about: Mapped[str | None] = mapped_column(String(500), nullable=True)
    university_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"), nullable=True)
    tg_username: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True, nullable=True)
    telegram_notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enrollment_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    past_tariffs: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cohort_tag: Mapped[str | None] = mapped_column(String(20), nullable=True)
    exam_dates: Mapped[str | None] = mapped_column(String(30), nullable=True)
    exam_subjects: Mapped[str | None] = mapped_column(String(20), nullable=True)
    study_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_publishable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    course_periods: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lessons_count: Mapped[str | None] = mapped_column(String(5), nullable=True)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_vk_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Таймстемпы активности/онбординга (для статистики; ставятся один раз, кроме last_login_at)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    portfolio_do_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # New fields (spec v1.0)
    drive_folder_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    staff_login: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    portfolio_do_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Точка А — одна оценка Главного преподавателя за ВЕСЬ набор работ «До»
    # (владелец 09.09.2026: «оценку должен ГП ставит общую по всем работам, не
    # для каждой… это будет для высчета среднего значения в точке А»). Шкала
    # 0–100, как у Work.score. Колонками на ученике, а не отдельной таблицей:
    # оценка ровно одна на ученика, как и соседний portfolio_do_completed.
    #
    # В user-dict сессии (`app/dependencies.py`) эти поля не кладутся: их
    # правит ГП у чужого ученика, а сбросить чужую сессию из Redis нечем —
    # `invalidate_session` берёт session_id того, кто пришёл. Нужен балл на
    # экране ученика — читать его из базы, а не из сессии.
    portfolio_before_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    portfolio_before_scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    portfolio_before_scored_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    curator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    curator_tag: Mapped[str | None] = mapped_column(String(100), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Архив прошлого потока: ученик убран из рабочих списков (вместе с is_active=False),
    # но работы, оценки и переписки целы и открыты суперадмину. Отдельно от deleted_at,
    # потому что тот снимается при любом входе (auth._upsert_user), а архив — нет.
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Срок доступа: наступил — ученик заходит, но видит только «Личную
    # информацию» со ссылкой на оплату и поддержку (владелец 11.09.2026, про
    # пробный набор предобучения 18-27.09: «доступ закрыт и он остаётся только
    # на экране Личная информация, ссылка на поддержку»). ЧЕТВЁРТОЕ состояние
    # рядом с блокировкой, архивом и soft-delete — и единственное, где вход
    # остаётся рабочим: те три закрывают кабинет целиком, а здесь человек
    # должен дочитать условия и оплатить.
    #
    # Поле намеренно НЕ про пробный период: та же дата закрывает доступ любому
    # ученику, который перестал платить. NULL — доступ бессрочный, так живут
    # все действующие ученики.
    #
    # Хранится в UTC, время суток значимо (отсечка «27 сентября 23:30»), —
    # поэтому конвертация через `app.services.tz.parse_msk_local`, как у
    # `TaskBlock.closes_at`, а не через `msk_midnight`.
    access_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    role = relationship("Role", lazy="select")

    __table_args__ = (
        Index("ix_users_active_role", "is_active", "role_id"),
        Index("ix_users_curator_active", "curator_id", "is_active"),
        Index("ix_users_deleted_active", "deleted_at", "is_active"),
    )
