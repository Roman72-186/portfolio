"""payment_link_url_text

12.10.2026, первая живая оплата владельца: «Оплатить» падал с 500 —
`payments.link_url` был `VARCHAR(500)`, а подписанная ссылка Продамуса
длиннее: название услуги по-русски в адресе кодируется по 6 символов на
букву, плюс телефон, почта и подпись — около 1 100 символов. Тесты на SQLite
длину строки не проверяют, поэтому ошибку показал только Postgres.
Ссылку храним целиком (её отдают повторно, пока жива) — поле без предела.

Revision ID: e5a7c9b1d3f6
Revises: d4f8b2a6c1e3
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5a7c9b1d3f6"
down_revision: Union[str, None] = "d4f8b2a6c1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "payments", "link_url",
        existing_type=sa.String(500), type_=sa.Text(), existing_nullable=True,
    )


def downgrade() -> None:
    # Длинную ссылку обрезать нельзя — она перестанет открываться; живую
    # ссылку при следующем «Оплатить» платформа соберёт заново.
    op.execute("UPDATE payments SET link_url = NULL WHERE length(link_url) > 500")
    op.alter_column(
        "payments", "link_url",
        existing_type=sa.Text(), type_=sa.String(500), existing_nullable=True,
    )
