"""tracker tasks and schedule digest

«Личный трекер» ученика (вкладка 1 из шести по макету созвона 17.08): задачи от
Главного преподавателя со своим состоянием у каждого ученика + адресное
дайджест-расписание месяца.

Одна миграция на все восемь таблиц намеренно: параллельно идёт вторая сессия по
уведомлениям, и каждая лишняя ревизия — лишний шанс словить две головы, из-за
которых `alembic upgrade head` в команде контейнера уронит старт приложения.

Ветвится от c1a8f5e6b3d2 (тумблер Telegram-уведомлений), а не от a6528d778ba0.

Revision ID: 7cee183db760
Revises: c1a8f5e6b3d2
Create Date: 2026-08-20 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7cee183db760'
down_revision: Union[str, None] = 'c1a8f5e6b3d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Задачи ───────────────────────────────────────────────────────────────
    op.create_table(
        "tracker_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subject", sa.String(length=50), nullable=True),
        sa.Column(
            "completion_mode",
            sa.String(length=20),
            nullable=False,
            server_default="auto_or_manual",
        ),
        sa.Column("source_kind", sa.String(length=30), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("assign_to_all", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["published_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tracker_tasks_public", "tracker_tasks", ["is_published", "due_at"])
    op.create_index("ix_tracker_tasks_source", "tracker_tasks", ["source_kind", "source_id"])

    op.create_table(
        "tracker_task_tags",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tracker_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id", "tag_id"),
    )
    op.create_index("ix_tracker_task_tags_tag", "tracker_task_tags", ["tag_id"])

    op.create_table(
        "tracker_task_assignees",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["task_id"], ["tracker_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id", "user_id"),
    )
    op.create_index("ix_tracker_task_assignees_user", "tracker_task_assignees", ["user_id"])

    op.create_table(
        "tracker_task_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_id", sa.Integer(), nullable=True),
        sa.Column("completion_source", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["task_id"], ["tracker_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["completed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "user_id", name="uq_tracker_task_state_task_user"),
    )
    op.create_index("ix_tracker_task_states_task_id", "tracker_task_states", ["task_id"])
    op.create_index("ix_tracker_task_states_user_id", "tracker_task_states", ["user_id"])
    op.create_index(
        "ix_tracker_task_states_user_status", "tracker_task_states", ["user_id", "status"]
    )

    # ── Дайджест-расписание ──────────────────────────────────────────────────
    # Уникальности по (year, month) нет намеренно: дайджестов на месяц несколько,
    # по одному на аудиторию (решение владельца 20.08 — расписание разное по
    # группам и тарифам).
    op.create_table(
        "schedule_digests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("assign_to_all", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["published_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_schedule_digests_period", "schedule_digests", ["year", "month", "is_published"]
    )

    op.create_table(
        "schedule_digest_tags",
        sa.Column("digest_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["digest_id"], ["schedule_digests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("digest_id", "tag_id"),
    )
    op.create_index("ix_schedule_digest_tags_tag", "schedule_digest_tags", ["tag_id"])

    op.create_table(
        "schedule_digest_assignees",
        sa.Column("digest_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["digest_id"], ["schedule_digests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("digest_id", "user_id"),
    )
    op.create_index(
        "ix_schedule_digest_assignees_user", "schedule_digest_assignees", ["user_id"]
    )

    op.create_table(
        "schedule_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("digest_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("meeting_url", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["digest_id"], ["schedule_digests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_schedule_events_digest_id", "schedule_events", ["digest_id"])
    op.create_index(
        "ix_schedule_events_digest_date", "schedule_events", ["digest_id", "starts_on"]
    )


def downgrade() -> None:
    op.drop_table("schedule_events")
    op.drop_table("schedule_digest_assignees")
    op.drop_table("schedule_digest_tags")
    op.drop_table("schedule_digests")
    op.drop_table("tracker_task_states")
    op.drop_table("tracker_task_assignees")
    op.drop_table("tracker_task_tags")
    op.drop_table("tracker_tasks")
