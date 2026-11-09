"""Independent portfolio admission for paid annual newcomers, 09.11.2026."""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "a61f0e9c73d2"
down_revision = "f7b31e0c942a"
branch_labels = None
depends_on = None


def seed_existing(conn):
    # A deliberately assigned personal 72h window keeps its opening time.
    # Ordinary block timers started by reading the feed are not intentional
    # openings of the new admission. No original states/works are rewritten.
    conn.execute(sa.text("""
        INSERT INTO portfolio_onboardings (user_id, payment_id, created_at, granted_at, started_at)
        SELECT u.id,
            (SELECT MIN(p.id) FROM payments p WHERE p.user_id = u.id
                AND p.kind = 'month' AND p.status = 'paid'),
            :now,
            CASE WHEN EXISTS (SELECT 1 FROM payments p WHERE p.user_id = u.id
                AND p.kind = 'month' AND p.status = 'paid') THEN :now ELSE NULL END,
            (SELECT MIN(s.started_at) FROM task_block_states s
                JOIN task_blocks b ON b.id = s.block_id
                JOIN task_block_students bs ON bs.block_id = b.id AND bs.user_id = u.id
                WHERE s.user_id = u.id AND b.block_type = 'portfolio'
                    AND b.portfolio_window_hours = 72 AND b.bypass_sequence = true
                    AND b.is_required = false)
        FROM users u JOIN roles r ON r.id = u.role_id
        WHERE r.rank = 1 AND u.program = 'annual' AND u.is_active = true
            AND u.archived_at IS NULL AND u.deleted_at IS NULL
            AND u.portfolio_do_completed = false
            AND (EXISTS (SELECT 1 FROM user_tags ut JOIN tags t ON t.id = ut.tag_id
                    WHERE ut.user_id = u.id AND t.name = 'Новенькие')
                OR EXISTS (SELECT 1 FROM public_enrollments pe WHERE pe.user_id = u.id))
            AND NOT EXISTS (SELECT 1 FROM works w WHERE w.user_id = u.id
                AND w.work_type = 'before' AND w.status = 'success')
            AND NOT EXISTS (SELECT 1 FROM portfolio_onboardings po WHERE po.user_id = u.id)
    """).bindparams(sa.bindparam("now", type_=sa.DateTime(timezone=True))),
        {"now": datetime.now(timezone.utc)})


def upgrade():
    op.create_table(
        "portfolio_onboardings",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
    )
    seed_existing(op.get_bind())


def downgrade():
    op.drop_table("portfolio_onboardings")
