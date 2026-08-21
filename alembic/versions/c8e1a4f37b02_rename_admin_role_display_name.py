"""Роль ранга 4 называется «Главный преподаватель»

Решение владельца от 21.08.2026: «главный преподаватель» и «админ» — одна и та
же роль ранга 4. Шестая роль не заводится, ранги не сдвигаются, меняется только
отображаемое имя.

Миграция нужна потому, что `seed_roles_and_permissions` создаёт роль лишь когда
её нет (`if name in existing_roles: continue`) и существующей `display_name` не
обновляет. Без этой миграции правка `ROLES` в `app/services/rbac.py` работает
только на пустой базе (тесты), а на проде в таблице `roles` осталось бы старое
«Админ» — и выпадающие списки, карточка пользователя и сводка по ролям в
кабинетах не изменились бы.

Фильтр по `name`, а не по `display_name` или рангу: `name` — это внутренний
ключ, по которому роль ищут и seed, и `ROLE_CABINET_MAP`. Фильтр по старому
`display_name` промолчал бы, если строку на проде уже правили руками.

Revision ID: c8e1a4f37b02
Revises: b7f21c93ad40
Create Date: 2026-08-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8e1a4f37b02'
down_revision: Union[str, None] = 'b7f21c93ad40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE roles SET display_name = :new WHERE name = :name")
        .bindparams(new='Главный преподаватель', name='админ')
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE roles SET display_name = :old WHERE name = :name")
        .bindparams(old='Админ', name='админ')
    )
