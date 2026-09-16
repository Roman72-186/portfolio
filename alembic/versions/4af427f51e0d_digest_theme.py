"""дайджест месяца: тема месяца отдельной колонкой

Роман 16.09.2026: «в дайджесте хочется название. Это может быть месяц плюс
тема месяца».

Колонка отдельная, а не переиспользованный `title`, потому что `title` уже
занят другим смыслом: им различают дайджесты одного месяца между собой по
аудитории. Плейсхолдер формы прямо этому учит — «Например: Сентябрь —
топ-тариф», то есть в прод-названиях уже лежит месяц и тариф. Склеивать
«Сентябрь · Сентябрь — топ-тариф» нельзя, разбирать строку на части при
рендере — тем более.

Так у дайджеста два имени с разными адресатами: `title` служебный, его видит
преподаватель в списке; `theme` — то, что читает ученик над календарём.
Существующие записи не трогаются: тема пуста, ученику показывается прежний
`title`.

Revision ID: 4af427f51e0d
Revises: e3a9d7c14b28
Create Date: 2026-09-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4af427f51e0d'
down_revision: Union[str, None] = 'e3a9d7c14b28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("schedule_digests", sa.Column("theme", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("schedule_digests", "theme")
