"""add revision_done_at to exam_cycles

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-07-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # «Завершить правку» теперь ставит revision_done_at вместо зануления
    # revision_requested_at; состояние «на правке» = requested IS NOT NULL AND done IS NULL.
    # Существующие данные консистентны: у всех висящих возвратов done_at = NULL.
    op.add_column("exam_cycles", sa.Column("revision_done_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("exam_cycles", "revision_done_at")
