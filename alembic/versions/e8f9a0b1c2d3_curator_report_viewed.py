"""add viewed_at / viewed_by_id to curator_reports

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-07-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("curator_reports", sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "curator_reports",
        sa.Column("viewed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("curator_reports", "viewed_by_id")
    op.drop_column("curator_reports", "viewed_at")
