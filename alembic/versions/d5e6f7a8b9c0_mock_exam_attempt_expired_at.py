"""mock_exam_attempts.expired_at

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa


revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `mock_exam_attempts` не заводится ни одной миграцией — тот же пробел,
    # что у exam_tickets/exam_assignments (a7b8c9d0e1f2) и users.parent_phone
    # (b2c3d4e5f6a7), найдено 30.08.2026 при поднятии чистого docker-compose.
    # На проде уже есть — no-op, на свежей базе — стаб с базовыми колонками
    # (expired_at ниже — единственное поле, которое реально добавляет эта
    # миграция и на проде тоже).
    op.execute(
        "CREATE TABLE IF NOT EXISTS mock_exam_attempts ("
        "id SERIAL PRIMARY KEY, "
        "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
        "subject VARCHAR(50) NOT NULL, "
        "ticket_id INTEGER REFERENCES exam_tickets(id) ON DELETE SET NULL, "
        "ticket_title VARCHAR(200) NOT NULL, "
        "ticket_description TEXT, "
        "ticket_image_url VARCHAR(500), "
        "started_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "completed_at TIMESTAMPTZ, "
        "notif_2h_sent BOOLEAN NOT NULL DEFAULT false, "
        "notif_3h_sent BOOLEAN NOT NULL DEFAULT false, "
        "notif_10min_sent BOOLEAN NOT NULL DEFAULT false, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )

    op.add_column(
        "mock_exam_attempts",
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mock_exam_attempts", "expired_at")
