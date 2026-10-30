"""lab card access

Revision ID: c3d1ab29e640
Revises: b8d2f4a61c90
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d1ab29e640"
down_revision = "b8d2f4a61c90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("lab_card_access", sa.Column("card_id", sa.String(150), primary_key=True))
    op.create_table("lab_card_tariffs",
        sa.Column("card_id", sa.String(150), sa.ForeignKey("lab_card_access.card_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tariff", sa.String(50), primary_key=True))
    op.create_table("lab_card_levels",
        sa.Column("card_id", sa.String(150), sa.ForeignKey("lab_card_access.card_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("level", sa.Integer(), primary_key=True),
        sa.CheckConstraint("level IN (1, 2)", name="ck_lab_card_levels_level"))
    op.create_table("lab_card_tags",
        sa.Column("card_id", sa.String(150), sa.ForeignKey("lab_card_access.card_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True))
    op.create_table("lab_card_students",
        sa.Column("card_id", sa.String(150), sa.ForeignKey("lab_card_access.card_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True))


def downgrade():
    op.drop_table("lab_card_students")
    op.drop_table("lab_card_tags")
    op.drop_table("lab_card_levels")
    op.drop_table("lab_card_tariffs")
    op.drop_table("lab_card_access")
