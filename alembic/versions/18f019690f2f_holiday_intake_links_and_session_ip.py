"""holiday_intake_links_and_session_ip

Ссылки групп каникулярного набора и адрес входа (созвон 13.10.2026, план
`plans/2026-10-14-apparchi-созвон-13-10.md`, часть 3.2).

`intake_links.program` — какую программу получит новичок по ссылке; всё, что
есть в базе (ссылка `/proba`), — годовое, `server_default` проставляет его без
бэкфилла. `intake_links.tag_id` — тег группы, по нему каникулярный цикл
открывается ученику. `sessions.ip` / `sessions.user_agent` — откуда вошли:
школа хочет видеть, не делят ли каникулярный доступ на несколько человек.

Revision ID: 18f019690f2f
Revises: 59d9d4fd36fd
Create Date: 2026-10-14 17:24:06.850322

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '18f019690f2f'
down_revision: Union[str, None] = '59d9d4fd36fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "intake_links",
        sa.Column("program", sa.String(length=20), nullable=False, server_default="annual"),
    )
    op.add_column(
        "intake_links",
        sa.Column("tag_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_intake_links_tag_id_tags", "intake_links", "tags",
        ["tag_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column("sessions", sa.Column("ip", sa.String(length=45), nullable=True))
    op.add_column("sessions", sa.Column("user_agent", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "user_agent")
    op.drop_column("sessions", "ip")
    op.drop_constraint("fk_intake_links_tag_id_tags", "intake_links", type_="foreignkey")
    op.drop_column("intake_links", "tag_id")
    op.drop_column("intake_links", "program")
