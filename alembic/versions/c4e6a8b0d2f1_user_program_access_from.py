"""user program_access_from

Владелец 29.09.2026: новые ученики годового курса не должны видеть циклы
предобучения, которые закончились до их прихода. Отметка «программа открыта
с» — у ученика. Колонка без значения по умолчанию: у всех, кто уже учится,
остаётся NULL, и они видят всё, как раньше.

Revision ID: c4e6a8b0d2f1
Revises: b3d5f7a9c1e2
Create Date: 2026-09-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e6a8b0d2f1"
down_revision: Union[str, None] = "b3d5f7a9c1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("program_access_from", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "program_access_from")
