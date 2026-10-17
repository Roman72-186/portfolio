"""holiday_track_payment

Оплата трека каникулярного набора на платформе (17.10.2026, план
`plans/2026-10-17-apparchi-оплата-трека.md`). Трек — ссылка группы
(`intake_links` с тегом): цена, что входит и два переключателя школы —
напоминания в Telegram и переписка с преподавателем («потенциал»). Платёж
трека — `payments.kind='holiday'` со ссылкой на трек. Данных не перекладывает:
у существующих ссылок цены нет — они ведут себя как прежде.

Revision ID: 535198ed8a18
Revises: 53c9ec345194
Create Date: 2026-10-17 16:06:50.452043

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '535198ed8a18'
down_revision: Union[str, None] = '53c9ec345194'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("intake_links", sa.Column("price_kop", sa.Integer(), nullable=True))
    op.add_column("intake_links", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "intake_links",
        sa.Column("reminders_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "intake_links",
        sa.Column("dialog_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("payments", sa.Column("intake_link_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_payments_intake_link_id_intake_links", "payments", "intake_links",
        ["intake_link_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_payments_intake_link_id_intake_links", "payments", type_="foreignkey")
    op.drop_column("payments", "intake_link_id")
    op.drop_column("intake_links", "dialog_enabled")
    op.drop_column("intake_links", "reminders_enabled")
    op.drop_column("intake_links", "description")
    op.drop_column("intake_links", "price_kop")
