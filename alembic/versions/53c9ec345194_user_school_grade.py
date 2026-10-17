"""user_school_grade

«Где учишься?» у ученика каникулярного набора (созвон 13.10.2026, план
`plans/2026-10-14-apparchi-созвон-13-10.md`, часть 3.3: «короткая анкета и
класс в виде тега»). Код варианта из `constants.SCHOOL_GRADES` — `grade9`,
`grade10`, `grade11`, `uni12`, `college` (варианты школы 16.10.2026; до этого
в этой же миграции было число 10/11 — ни на одну базу прода она тогда не
выкатывалась, поэтому колонка поменяна здесь, а не новой миграцией). Пусто
у всех, кто уже в базе: годовая анкета это не спрашивает. Тег с подписью
варианта ставит анкета (`tags.ensure_grade_tag`), колонка остаётся источником
правды.

Revision ID: 53c9ec345194
Revises: 18f019690f2f
Create Date: 2026-10-14 23:39:04.725623

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '53c9ec345194'
down_revision: Union[str, None] = '18f019690f2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("school_grade", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "school_grade")
