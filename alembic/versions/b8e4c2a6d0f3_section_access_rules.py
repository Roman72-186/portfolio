"""section_access_rules

Переключатели доступа к разделам кабинета (владелец 30.09.2026): суперадмин
закрывает раздел роли целиком или отдельному сотруднику. Только сужение —
потолок задаёт ранг роли. Каталог разделов и проверка —
`app/services/section_access.py`.

Revision ID: b8e4c2a6d0f3
Revises: e7b3d9f5a1c4
Create Date: 2026-09-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e4c2a6d0f3"
down_revision: Union[str, None] = "e7b3d9f5a1c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "section_access_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("section_key", sa.String(40), nullable=False),
        sa.Column(
            "role_id", sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(role_id IS NULL) <> (user_id IS NULL)",
            name="ck_section_access_rules_one_target",
        ),
        sa.UniqueConstraint("section_key", "role_id", name="uq_section_access_rules_role"),
        sa.UniqueConstraint("section_key", "user_id", name="uq_section_access_rules_user"),
    )


def downgrade() -> None:
    op.drop_table("section_access_rules")
