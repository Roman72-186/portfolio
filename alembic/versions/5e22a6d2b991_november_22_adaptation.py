"""Assign the November 22 annual intake its own adaptation tag."""
from alembic import op
import sqlalchemy as sa

revision = "5e22a6d2b991"
down_revision = "0fa652cb093e"
branch_labels = depends_on = None

TAG = "Адаптация · 22 ноября 2026"
CYCLE = 105


def upgrade():
    db = op.get_bind()
    cycle_exists = db.execute(sa.text("""
        SELECT 1 FROM learning_topics
        WHERE id = :cycle AND title = 'ноябрь_адаптация' AND kind = 'week'
          AND deleted_at IS NULL AND ends_at IS NOT NULL
    """), {"cycle": CYCLE}).scalar()
    if not cycle_exists:
        return
    db.execute(sa.text("""
        INSERT INTO tags (name, created_at) VALUES (:tag, now())
        ON CONFLICT (name) DO NOTHING
    """), {"tag": TAG})
    db.execute(sa.text("""
        INSERT INTO learning_topic_tags (topic_id, tag_id)
        SELECT :cycle, id FROM tags WHERE name = :tag
        ON CONFLICT DO NOTHING
    """), {"cycle": CYCLE, "tag": TAG})
    db.execute(sa.text("""
        INSERT INTO user_tags (user_id, tag_id, created_at)
        SELECT u.id, t.id, now()
        FROM users u
        JOIN roles r ON r.id = u.role_id
        CROSS JOIN tags t
        WHERE t.name = :tag AND r.rank = 1 AND u.program = 'annual'
          AND u.tariff IN ('Я САМ', 'Я С ВАМИ', 'УВЕРЕННЫЙ МАКСИМУМ')
          AND (u.program_access_from AT TIME ZONE 'Europe/Moscow')::date = DATE '2026-11-22'
        ON CONFLICT DO NOTHING
    """), {"tag": TAG})


def downgrade():
    db = op.get_bind()
    for table in ("user_tags", "learning_topic_tags"):
        db.execute(sa.text(f"DELETE FROM {table} WHERE tag_id IN (SELECT id FROM tags WHERE name = :tag)"),
                   {"tag": TAG})
