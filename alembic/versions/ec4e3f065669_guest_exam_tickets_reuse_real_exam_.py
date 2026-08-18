"""guest exam tickets reuse real exam_tickets

Владелец пересмотрел решение 18.08.2026: билеты гостевого режима больше не
отдельная таблица `guest_tickets`, а настоящие `ExamTicket`/`ExamAssignment`
с `kind="guest"` — та же логика, что у реального пробника. Изоляция от
настоящих учеников теперь держится на фильтре `kind != "guest"` во всех
резолверах (см. app/constants.py, app/services/exam_cycle.py,
app/services/exam_scheduler.py, app/api/cabinet_superadmin.py), а не на
отдельной таблице.

На проде на момент миграции ни одного гостевого билета/ссылки ещё не создано
(см. session-handoffs/current.md) — `ticket_id` в guest_submissions обнуляется
на всякий случай перед сменой внешнего ключа, но по факту пустой.

Revision ID: ec4e3f065669
Revises: a2c3d4e5f6b7
Create Date: 2026-08-18 14:13:49.735993

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ec4e3f065669'
down_revision: Union[str, None] = 'a2c3d4e5f6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE guest_submissions SET ticket_id = NULL")

    op.drop_constraint("guest_submissions_ticket_id_fkey", "guest_submissions", type_="foreignkey")

    op.drop_index("ix_guest_tickets_config_subject", table_name="guest_tickets")
    op.drop_index("ix_guest_tickets_config_id", table_name="guest_tickets")
    op.drop_table("guest_tickets")

    op.create_foreign_key(
        "guest_submissions_ticket_id_fkey",
        "guest_submissions", "exam_tickets",
        ["ticket_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("guest_submissions_ticket_id_fkey", "guest_submissions", type_="foreignkey")

    op.create_table(
        "guest_tickets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_s3_url", sa.String(length=500), nullable=True),
        sa.Column("image_s3_path", sa.String(length=300), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["config_id"], ["guest_exam_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guest_tickets_config_id", "guest_tickets", ["config_id"])
    op.create_index(
        "ix_guest_tickets_config_subject", "guest_tickets", ["config_id", "subject", "is_active"]
    )

    op.execute("UPDATE guest_submissions SET ticket_id = NULL")
    op.create_foreign_key(
        "guest_submissions_ticket_id_fkey",
        "guest_submissions", "guest_tickets",
        ["ticket_id"], ["id"], ondelete="SET NULL",
    )
