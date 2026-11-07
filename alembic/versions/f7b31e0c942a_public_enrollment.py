"""Покупка годового курса до вступления в канал, 07.11.2026."""
import sqlalchemy as sa
from alembic import op

revision = "f7b31e0c942a"
down_revision = "c3d1ab29e640"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "public_enrollments",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("selected_tariff", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="RESTRICT")),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("public_enrollments")
