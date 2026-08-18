"""learning topic meeting url

Кнопка «Занятие» внутри недели «Актуального образовательного пространства»
(кабинет ученика, трек A) ведёт на созвон недели — нужно поле для ссылки.

Revision ID: ad76cd2dd825
Revises: de86dfda0710
Create Date: 2026-08-18 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ad76cd2dd825'
down_revision: Union[str, None] = 'de86dfda0710'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("learning_topics", sa.Column("meeting_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("learning_topics", "meeting_url")
