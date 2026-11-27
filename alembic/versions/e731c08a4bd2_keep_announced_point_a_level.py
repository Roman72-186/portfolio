"""Preserve the most recently announced program level, 11.11.2026."""
from alembic import op
import sqlalchemy as sa

revision = "e731c08a4bd2"
down_revision = "a103c7d9e5f2"
branch_labels = None
depends_on = None


def seed_announced_levels(conn):
    # Exact titles identify the system notices. Use the last one if a teacher
    # explicitly sent the level again. A broadcast with the same title is not
    # an assignment of a level.
    conn.execute(sa.text("""
        UPDATE users
        SET point_a_assigned_level = (
            SELECT CASE n.title
                WHEN 'Точка А разобрана — уровень 1' THEN 1
                WHEN 'Точка А разобрана — уровень 2' THEN 2
            END
            FROM notifications n
            WHERE n.user_id = users.id AND n.broadcast_id IS NULL
                AND n.title IN ('Точка А разобрана — уровень 1', 'Точка А разобрана — уровень 2')
            ORDER BY n.created_at DESC, n.id DESC
            LIMIT 1
        )
        WHERE point_a_assigned_level IS NULL
            AND id IN (
                SELECT user_id FROM notifications WHERE broadcast_id IS NULL
                    AND title IN ('Точка А разобрана — уровень 1', 'Точка А разобрана — уровень 2')
            )
    """))


def upgrade():
    op.add_column("users", sa.Column(
        "point_a_assigned_level", sa.Integer(),
        sa.CheckConstraint("point_a_assigned_level IN (1, 2)", name="ck_users_point_a_assigned_level"),
        nullable=True,
    ))
    seed_announced_levels(op.get_bind())


def downgrade():
    op.drop_column("users", "point_a_assigned_level")
