"""task_block_required_tariffs

Кого обязать выполнить блок — отдельная ось от видимости (владелец
10.09.2026): «Тарифы» у блока (`task_block_tariffs`) решают, кому блок
виден; эта таблица — с кого требовать выполнение, если блок обязательный
(`is_required=True`). Пусто = обязательно всем, кому видно — то же
поведение, что было до этой миграции, обратная совместимость без бэкфилла.

Один в один зеркало `task_block_tariffs` (миграция `7982848a4a01`).

Revision ID: da35b5b9d126
Revises: a3ad0a150d1f
Create Date: 2026-09-10 15:41:37.910093

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'da35b5b9d126'
down_revision: Union[str, None] = 'a3ad0a150d1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_block_required_tariffs",
        sa.Column(
            "block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("tariff", sa.String(length=50), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("task_block_required_tariffs")
