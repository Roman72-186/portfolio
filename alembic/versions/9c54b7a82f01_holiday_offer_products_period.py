"""Move the 29 November offer into the information products program."""
from alembic import op
from sqlalchemy import text

revision = "9c54b7a82f01"
down_revision = "c84f6d8e1702"
branch_labels = None
depends_on = None


def _periods(db):
    main = db.execute(text("""
        SELECT id FROM learning_topics
        WHERE kind = 'period' AND program = 'holiday' AND parent_id IS NULL
          AND title = :title AND deleted_at IS NULL
    """), {"title": "Информационные продукты"}).scalar_one_or_none()
    shell = db.execute(text("""
        SELECT topic.id FROM learning_topics topic
        JOIN learning_topic_tags tag ON tag.topic_id = topic.id
        JOIN intake_links link ON link.tag_id = tag.tag_id
        WHERE link.slug = 'testpodgotovki-29' AND topic.kind = 'period'
          AND topic.title = :title
    """), {"title": "Попробуй подготовку · 29 ноября"}).scalar_one_or_none()
    return main, shell


def _move(source, target, title):
    op.get_bind().execute(text("""
        UPDATE learning_topics SET parent_id = :target, title = :title
        WHERE kind = 'stage' AND parent_id = :source AND deleted_at IS NULL
          AND id IN (
              SELECT tag.topic_id FROM learning_topic_tags tag
              JOIN intake_links link ON link.tag_id = tag.tag_id
              WHERE link.slug = 'testpodgotovki-29'
          )
    """), {"source": source, "target": target, "title": title})


def upgrade():
    db = op.get_bind()
    main, shell = _periods(db)
    if main is None or shell is None:
        return
    _move(shell, main, "Попробуй подготовку · 29 ноября")
    db.execute(text("""
        UPDATE learning_topics SET deleted_at = CURRENT_TIMESTAMP
        WHERE id = :shell AND deleted_at IS NULL AND NOT EXISTS (
            SELECT 1 FROM learning_topics WHERE parent_id = :shell AND deleted_at IS NULL
        )
    """), {"shell": shell})


def downgrade():
    db = op.get_bind()
    main, shell = _periods(db)
    if main is None or shell is None:
        return
    _move(main, shell, "Пробная неделя")
    db.execute(text("UPDATE learning_topics SET deleted_at = NULL WHERE id = :shell"), {"shell": shell})
