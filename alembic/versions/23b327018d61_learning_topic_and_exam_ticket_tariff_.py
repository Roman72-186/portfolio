"""learning topic and exam ticket tariff visibility

Revision ID: 23b327018d61
Revises: 841212ccdafd
Create Date: 2026-08-30 22:36:15.831358

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '23b327018d61'
down_revision: Union[str, None] = '841212ccdafd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "learning_topics",
        sa.Column(
            "tariff_restricted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_table(
        "learning_topic_tariffs",
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("tariff", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["learning_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("topic_id", "tariff"),
    )
    op.create_index(
        "ix_learning_topic_tariffs_tariff", "learning_topic_tariffs", ["tariff"]
    )

    op.add_column(
        "exam_tickets",
        sa.Column(
            "tariff_restricted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_table(
        "exam_ticket_tariffs",
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("tariff", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["exam_tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ticket_id", "tariff"),
    )
    op.create_index(
        "ix_exam_ticket_tariffs_tariff", "exam_ticket_tariffs", ["tariff"]
    )


def downgrade() -> None:
    op.drop_index("ix_exam_ticket_tariffs_tariff", table_name="exam_ticket_tariffs")
    op.drop_table("exam_ticket_tariffs")
    op.drop_column("exam_tickets", "tariff_restricted")

    op.drop_index("ix_learning_topic_tariffs_tariff", table_name="learning_topic_tariffs")
    op.drop_table("learning_topic_tariffs")
    op.drop_column("learning_topics", "tariff_restricted")
