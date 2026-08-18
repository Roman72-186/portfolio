"""guest exam module (temporary, guest trial window 26-28 aug 2026)

Изолированный временный модуль для гостевого доступа к пробнику — участники без
полноценной регистрации получают билет по общей ссылке, сдают работу, видят балл
и комментарий куратора. Ни одна из четырёх таблиц не связана с users.vk_id/
sessions/exam_tickets/mock_exam_attempts — см. plans/2026-08-18-apparchi-student-
cabinet-and-guest-trial.md, трек B. Снести отдельной миграцией после того, как
результаты экспортированы и владелец подтвердил, что данные больше не нужны.

Revision ID: 9b1c2d3e4f5a
Revises: 8a7b6c5d4e3f
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9b1c2d3e4f5a"
down_revision: Union[str, None] = "8a7b6c5d4e3f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "guest_exam_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_guest_exam_configs_token"),
    )
    op.create_index("ix_guest_exam_configs_token", "guest_exam_configs", ["token"])

    op.create_table(
        "guest_tickets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_s3_url", sa.String(length=500), nullable=True),
        sa.Column("image_s3_path", sa.String(length=300), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["config_id"], ["guest_exam_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guest_tickets_config_id", "guest_tickets", ["config_id"])
    op.create_index(
        "ix_guest_tickets_config_subject", "guest_tickets", ["config_id", "subject", "is_active"]
    )

    op.create_table(
        "guest_participants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("participant_code", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["config_id"], ["guest_exam_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("participant_code", name="uq_guest_participants_code"),
    )
    op.create_index("ix_guest_participants_config_id", "guest_participants", ["config_id"])
    op.create_index("ix_guest_participants_code", "guest_participants", ["participant_code"])

    op.create_table(
        "guest_submissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=50), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("ticket_title", sa.String(length=200), nullable=False),
        sa.Column("ticket_description", sa.Text(), nullable=True),
        sa.Column("ticket_image_url", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("s3_url", sa.String(length=500), nullable=True),
        sa.Column("s3_path", sa.String(length=300), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("scored_by_id", sa.Integer(), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="issued", nullable=False),
        sa.ForeignKeyConstraint(["participant_id"], ["guest_participants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["guest_tickets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scored_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "participant_id", "subject", name="uq_guest_submission_participant_subject"
        ),
    )
    op.create_index("ix_guest_submissions_participant_id", "guest_submissions", ["participant_id"])
    op.create_index("ix_guest_submissions_status", "guest_submissions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_guest_submissions_status", table_name="guest_submissions")
    op.drop_index("ix_guest_submissions_participant_id", table_name="guest_submissions")
    op.drop_table("guest_submissions")

    op.drop_index("ix_guest_participants_code", table_name="guest_participants")
    op.drop_index("ix_guest_participants_config_id", table_name="guest_participants")
    op.drop_table("guest_participants")

    op.drop_index("ix_guest_tickets_config_subject", table_name="guest_tickets")
    op.drop_index("ix_guest_tickets_config_id", table_name="guest_tickets")
    op.drop_table("guest_tickets")

    op.drop_index("ix_guest_exam_configs_token", table_name="guest_exam_configs")
    op.drop_table("guest_exam_configs")
