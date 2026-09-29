"""tag is_hidden

Владелец 29.09.2026: теги, заведённые в июне под прошлый поток, скрыть. На
проде это все 18 тегов (11–15.06.2026): 530 привязок из 548 — у архива, у
живых учеников 6. Флаг прячет тег из выпадающих списков, подсказок и чипов;
привязки и доступ по тегам не трогаются. Граница — 01.09.2026: всё, что
заведено раньше, относится к июньскому потоку.

Revision ID: f7c2a9d4b6e1
Revises: e1a3c5e7f9b2
Create Date: 2026-09-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7c2a9d4b6e1"
down_revision: Union[str, None] = "e1a3c5e7f9b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tags",
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE tags SET is_hidden = true WHERE created_at < '2026-09-01T00:00:00+03:00'"
    )


def downgrade() -> None:
    op.drop_column("tags", "is_hidden")
