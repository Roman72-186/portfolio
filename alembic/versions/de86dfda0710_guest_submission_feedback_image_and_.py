"""guest submission feedback image

Владелец пересмотрел UI проверки сдач: вернул её на страницу «Гостевой режим»
(вкладка «Работы», список участников с раскрытием по предмету) и добавил
необязательное фото обратной связи как альтернативу/дополнение к текстовому
комментарию.

Revision ID: de86dfda0710
Revises: ec4e3f065669
Create Date: 2026-08-18 14:52:24.657683

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'de86dfda0710'
down_revision: Union[str, None] = 'ec4e3f065669'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("guest_submissions", sa.Column("feedback_image_url", sa.String(length=500), nullable=True))
    op.add_column("guest_submissions", sa.Column("feedback_image_path", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("guest_submissions", "feedback_image_path")
    op.drop_column("guest_submissions", "feedback_image_url")
