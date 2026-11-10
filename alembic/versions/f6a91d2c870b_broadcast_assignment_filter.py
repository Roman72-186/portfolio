"""Фильтр рассылки по сдаче работы, 10.11.2026."""
from alembic import op
import sqlalchemy as sa

revision = "f6a91d2c870b"
down_revision = "e94a2b6c380f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("broadcasts") as batch:
        batch.add_column(sa.Column("assignment_block_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("assignment_status", sa.String(20), nullable=True))
        batch.create_check_constraint(
            "ck_broadcasts_assignment_filter",
            "(assignment_block_id IS NULL AND assignment_status IS NULL) OR "
            "(assignment_block_id IS NOT NULL AND assignment_block_id > 0 AND "
            "assignment_status IS NOT NULL AND assignment_status IN ('submitted', 'not_submitted'))",
        )


def downgrade():
    with op.batch_alter_table("broadcasts") as batch:
        batch.drop_constraint("ck_broadcasts_assignment_filter", type_="check")
        batch.drop_column("assignment_status")
        batch.drop_column("assignment_block_id")
