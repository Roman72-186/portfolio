"""guest exam: ссылка бессрочная (убрать окно дат), лог входов

Владелец решил: ссылка не ограничена по времени — только ручной вкл/выкл через
is_active, но каждый вход логируется для минимальной статистики.

Revision ID: a2c3d4e5f6b7
Revises: 9b1c2d3e4f5a
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2c3d4e5f6b7"
down_revision: Union[str, None] = "9b1c2d3e4f5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("guest_exam_configs", "starts_at")
    op.drop_column("guest_exam_configs", "ends_at")

    op.create_table(
        "guest_visits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["config_id"], ["guest_exam_configs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["participant_id"], ["guest_participants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guest_visits_config", "guest_visits", ["config_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_guest_visits_config", table_name="guest_visits")
    op.drop_table("guest_visits")

    op.add_column(
        "guest_exam_configs",
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column(
        "guest_exam_configs",
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
