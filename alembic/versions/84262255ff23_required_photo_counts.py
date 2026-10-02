"""required_photo_counts

«Сколько фото сдать» (владелец 02.10.2026, план
`plans/2026-10-02-apparchi-photo-count-setting.md`): дети присылали в
контрольную два-три снимка вместо одного, где сразу оба эскиза.

- `task_blocks.required_photos` — ровно столько фото принимает сдача в блоке
  (контрольная на время, «Домашнее задание»); работа сдана только при N из N;
- `exam_tickets.required_stage_photos` — ровно столько этапных фото в пробнике,
  финальное по-прежнему одно.

NULL — прежнее поведение (до 10 фото, сдано с первого). Бэкфилла нет: уже
сданные работы правило не трогает.

Revision ID: 84262255ff23
Revises: c45616212c1d
Create Date: 2026-10-02 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '84262255ff23'
down_revision: Union[str, None] = 'c45616212c1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("task_blocks", sa.Column("required_photos", sa.Integer(), nullable=True))
    op.add_column(
        "exam_tickets", sa.Column("required_stage_photos", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("exam_tickets", "required_stage_photos")
    op.drop_column("task_blocks", "required_photos")
