"""add sent_to_retake_at, needs_revision_at, updated_at to works

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-07-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("works", sa.Column("sent_to_retake_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("works", sa.Column("needs_revision_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("works", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    # Для существующих строк точное время последнего изменения неизвестно —
    # берём created_at как нижнюю границу.
    op.execute("UPDATE works SET updated_at = created_at WHERE updated_at IS NULL")


def downgrade() -> None:
    op.drop_column("works", "updated_at")
    op.drop_column("works", "needs_revision_at")
    op.drop_column("works", "sent_to_retake_at")
