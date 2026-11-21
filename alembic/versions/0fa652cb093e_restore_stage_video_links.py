"""Reconnect the three drawing stages to their published replacements."""
from alembic import op
import sqlalchemy as sa

revision = "0fa652cb093e"
down_revision = "a3829fc81d50"
branch_labels = depends_on = None


def upgrade():
    conn = op.get_bind()
    for block, old, new, original, replacement in (
        (411, 107, 113, "IMG_5127.mov", "А3_рисунок 1.mp4"),
        (415, 108, 114, "IMG_5128.mov", "А3_рисунок 2.mp4"),
        (416, 109, 112, "IMG_5129.mov", "А3_рисунок 3.mp4"),
    ):
        conn.execute(sa.text("""
            UPDATE task_blocks SET video_id = :new
            WHERE task_id = 168 AND id = :block AND block_type = 'photo_upload'
              AND video_id = :old
              AND EXISTS (SELECT 1 FROM learning_videos WHERE id = :old
                          AND original_filename = :original AND deleted_at IS NOT NULL)
              AND EXISTS (SELECT 1 FROM learning_videos WHERE id = :new
                          AND original_filename = :replacement AND deleted_at IS NULL
                          AND is_published AND status = 'ready')
        """), dict(block=block, old=old, new=new, original=original, replacement=replacement))


def downgrade():
    # Rolling back application code must not reattach deleted videos.
    pass
