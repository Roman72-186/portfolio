"""Convert four self-check steps without rewriting works or progress, 10.11.2026."""
from alembic import op
import sqlalchemy as sa

revision = "c82e1d6b904a"
down_revision = "f6a91d2c870b"
branch_labels = None
depends_on = None

TARGETS = ((378, 162), (380, 162), (381, 163), (384, 163))
LABEL = "Загрузить работы"


def convert_existing(conn):
    # Production IDs are frozen deliberately. Missing rows in other installs
    # are a no-op. Preserve video, dates, audience, states and original Work rows.
    for block_id, task_id in TARGETS:
        conn.execute(sa.text("""
            UPDATE task_blocks SET block_type = 'photo_upload', upload_button_label = :label
            WHERE id = :block_id AND task_id = :task_id AND block_type = 'portfolio'
                AND title IN ('Самопроверка компоновки', 'Самопроверка построения')
        """), {"label": LABEL, "block_id": block_id, "task_id": task_id})


def upgrade():
    op.add_column("task_blocks", sa.Column("upload_button_label", sa.String(200), nullable=True))
    convert_existing(op.get_bind())


def downgrade():
    # New submissions and historical files stay intact even on rollback.
    conn = op.get_bind()
    for block_id, task_id in TARGETS:
        conn.execute(sa.text("""
            UPDATE task_blocks SET block_type = 'portfolio'
            WHERE id = :block_id AND task_id = :task_id
                AND block_type = 'photo_upload' AND upload_button_label = :label
        """), {"label": LABEL, "block_id": block_id, "task_id": task_id})
    op.drop_column("task_blocks", "upload_button_label")
