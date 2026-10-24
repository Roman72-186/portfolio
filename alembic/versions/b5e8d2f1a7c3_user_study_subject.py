"""Предмет обучения ученика (24.10.2026)

Александрия 23.10.2026: ученик занимается только одним предметом — рисунком
или композицией, «Общее» ему нужно. Новое поле `users.study_subject`: пусто —
все предметы (так у всех), иначе программа показывает только этот предмет и
«Общее» (`task_blocks.is_block_open_to`). Данные не перекладываются.

Revision ID: b5e8d2f1a7c3
Revises: 4a83c858efbb
Create Date: 2026-10-24 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5e8d2f1a7c3'
down_revision: Union[str, None] = '4a83c858efbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("study_subject", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "study_subject")
