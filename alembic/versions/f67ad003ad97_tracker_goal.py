"""tracker goal

«Ближайшая цель» на Личном трекере — ручная карточка Главного преподавателя
(решение владельца 23.08: не производная от гейта, отдельная небольшая
сущность). Адресация та же тройка, что у задач и дайджеста: assign_to_all +
теги + поимённые исключения.

Revision ID: f67ad003ad97
Revises: ce18a435b332
Create Date: 2026-08-23 22:28:31.851146

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f67ad003ad97'
down_revision: Union[str, None] = 'ce18a435b332'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tracker_goals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_score", sa.Integer(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
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
    op.create_index("ix_tracker_goals_public", "tracker_goals", ["is_published", "starts_on"])

    op.create_table(
        "tracker_goal_tags",
        sa.Column("goal_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["goal_id"], ["tracker_goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("goal_id", "tag_id"),
    )
    op.create_index("ix_tracker_goal_tags_tag", "tracker_goal_tags", ["tag_id"])

    op.create_table(
        "tracker_goal_assignees",
        sa.Column("goal_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["goal_id"], ["tracker_goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("goal_id", "user_id"),
    )
    op.create_index("ix_tracker_goal_assignees_user", "tracker_goal_assignees", ["user_id"])


def downgrade() -> None:
    op.drop_table("tracker_goal_assignees")
    op.drop_table("tracker_goal_tags")
    op.drop_table("tracker_goals")
