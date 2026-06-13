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
    # New fields (spec v1.0)
    drive_folder_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    staff_login: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    portfolio_do_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    curator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    curator_tag: Mapped[str | None] = mapped_column(String(100), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
