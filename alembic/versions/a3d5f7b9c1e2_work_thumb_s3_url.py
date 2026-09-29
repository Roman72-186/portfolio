"""work thumb_s3_url

Превью 320px для квадратиков карточки ученика (план
`plans/2026-09-29-apparchi-students-phone.md`, шаг 5): в квадрат 84px грузилось
фото 1600px. Владелец 29.09.2026 выбрал превью только для новых работ, старые
не пересчитываются — у них колонка пустая, и экран показывает само фото.

Revision ID: a3d5f7b9c1e2
Revises: f7c2a9d4b6e1
Create Date: 2026-09-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3d5f7b9c1e2"
down_revision: Union[str, None] = "f7c2a9d4b6e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("works", sa.Column("thumb_s3_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("works", "thumb_s3_url")
