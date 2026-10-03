"""learning_topic locks_next

Владелец 03.10.2026: в настройке цикла, рядом с «Показывать ученикам», —
запирает ли незакрытый цикл доступ к следующим. С 30.09.2026 запирали все
циклы (`tracker.cycle_debt`), поэтому существующим ставится True и
поведение у учеников не меняется.

Revision ID: a3c7e9b1d5f2
Revises: d5e1a7c3b902
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3c7e9b1d5f2"
down_revision: Union[str, None] = "d5e1a7c3b902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "learning_topics",
        sa.Column("locks_next", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("learning_topics", "locks_next")
