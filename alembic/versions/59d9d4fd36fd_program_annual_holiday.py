"""program_annual_holiday

Две программы школы — годовой курс и каникулярный набор (созвон 13.10.2026,
план `plans/2026-10-14-apparchi-созвон-13-10.md`, часть 3.1). Признак у
периода (`learning_topics.program`) и у ученика (`users.program`). Всё, что
есть в базе, — годовое: `server_default` проставляет «annual» существующим
строкам, бэкфилла нет.

Revision ID: 59d9d4fd36fd
Revises: 0ab62c0706c3
Create Date: 2026-10-14 16:40:12.512301

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '59d9d4fd36fd'
down_revision: Union[str, None] = '0ab62c0706c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "learning_topics",
        sa.Column("program", sa.String(length=20), nullable=False, server_default="annual"),
    )
    op.add_column(
        "users",
        sa.Column("program", sa.String(length=20), nullable=False, server_default="annual"),
    )


def downgrade() -> None:
    op.drop_column("users", "program")
    op.drop_column("learning_topics", "program")
