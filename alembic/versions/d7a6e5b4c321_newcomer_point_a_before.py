"""Initial portfolio alone defines newcomer assessment, 10.11.2026."""
from alembic import op
import sqlalchemy as sa

revision = "d7a6e5b4c321"
down_revision = "c82e1d6b904a"
branch_labels = None
depends_on = None


def seed_existing(conn):
    # Freeze current enrollment membership; future tag changes are unrelated
    # to initial assessment. No scores, works, progress or files are rewritten.
    conn.execute(sa.text("""
        UPDATE users SET point_a_before_only = true
        WHERE program = 'annual' AND (
            EXISTS (SELECT 1 FROM user_tags ut JOIN tags t ON t.id = ut.tag_id
                    WHERE ut.user_id = users.id AND t.name = 'Новенькие')
            OR EXISTS (SELECT 1 FROM portfolio_onboardings po WHERE po.user_id = users.id)
            OR EXISTS (SELECT 1 FROM public_enrollments pe WHERE pe.user_id = users.id)
        )
    """))
    # Frozen IDs of the current first-week curriculum. Restrict both IDs and
    # parent topic so another installation cannot accidentally change tasks.
    conn.execute(sa.text("""
        UPDATE tracker_tasks SET include_in_newcomer_after = false
        WHERE id IN (156,157,159,160) AND topic_id = 90
    """))


def upgrade():
    op.add_column("users", sa.Column("point_a_before_only", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tracker_tasks", sa.Column("include_in_newcomer_after", sa.Boolean(), nullable=False, server_default=sa.true()))
    seed_existing(op.get_bind())


def downgrade():
    op.drop_column("tracker_tasks", "include_in_newcomer_after")
    op.drop_column("users", "point_a_before_only")
