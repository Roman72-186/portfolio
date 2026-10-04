"""section access levels

Тонкие настройки доступа (владелец 04.10.2026): вместо галочки «открыт /
закрыт» у раздела три уровня — `none` (нет), `view` (смотреть), `edit`
(менять). `section_access_rules.is_open` заменяется на `level`.

Перенос без смены поведения (план `plans/2026-10-04-apparchi-тонкие-доступы.md`,
развилка 2):

- `is_open = false` → `none`;
- строка роли модератора → `view`: галочка роли открывала модераторам раздел
  только на чтение (`moderator_may_use`, решение 28.09.2026);
- разделы без единого адреса на запись (архив, статистика, проверка
  пробников, 3D Лаб) → `view`: там «смотреть» и «менять» не отличаются;
- остальное → `edit`: открытый раздел пускал на все методы.

Прод 04.10.2026 — четыре строки, все «открыт»: архив лично 169 → `view`,
АОП и 3D Лаб роли модераторов → `view`, АОП лично 278 → `edit`.

Откат сводит уровни обратно к галочке (`none` → закрыт, остальное →
открыт): разница «смотреть / менять» у личных строк при этом теряется.

Revision ID: 639c04979ebf
Revises: b4d8f1a6c2e9
Create Date: 2026-10-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "639c04979ebf"
down_revision: Union[str, None] = "b4d8f1a6c2e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Снимок на 04.10.2026: разделы, у которых нет ни одного адреса на запись.
# Из кода приложения не импортируется — миграция не должна меняться вместе
# с каталогом разделов.
VIEW_ONLY_SECTIONS = ("archive", "statistics", "mock_check", "lab3d")

LEVEL_SQL = f"""
UPDATE section_access_rules SET level = CASE
    WHEN NOT is_open THEN 'none'
    WHEN section_key IN ({", ".join(f"'{k}'" for k in VIEW_ONLY_SECTIONS)}) THEN 'view'
    WHEN role_id IN (SELECT id FROM roles WHERE name = 'модератор') THEN 'view'
    ELSE 'edit'
END
"""


def upgrade() -> None:
    op.add_column(
        "section_access_rules",
        sa.Column("level", sa.String(8), nullable=False, server_default="none"),
    )
    op.execute(LEVEL_SQL)
    with op.batch_alter_table("section_access_rules") as batch:
        batch.drop_column("is_open")
        batch.create_check_constraint(
            "ck_section_access_rules_level", "level IN ('none', 'view', 'edit')",
        )


def downgrade() -> None:
    op.add_column(
        "section_access_rules",
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE section_access_rules SET is_open = (level <> 'none')")
    with op.batch_alter_table("section_access_rules") as batch:
        batch.drop_constraint("ck_section_access_rules_level", type_="check")
        batch.drop_column("level")
