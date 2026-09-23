"""add is_diagnostic to task_blocks, backfill from legacy archi_profile tasks

Revision ID: a7d363b096bd
Revises: dadc28ad26d8
Create Date: 2026-09-24

"""
from alembic import op
import sqlalchemy as sa

revision = 'a7d363b096bd'
down_revision = 'dadc28ad26d8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'task_blocks',
        sa.Column('is_diagnostic', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Безопасный бэкофилл: у существующих задач kind=archi_profile все
    # блоки-вопросы (block_type=question) и есть диагностика —
    # `result_for_answers` уже требует точного совпадения их числа с
    # `diagnostic_config["questions"]`, посторонних вопросов там быть не
    # может (см. app/services/archi_profile.py:result_for_answers).
    op.execute(
        """
        UPDATE task_blocks
        SET is_diagnostic = true
        FROM tracker_tasks
        WHERE task_blocks.task_id = tracker_tasks.id
          AND tracker_tasks.kind = 'archi_profile'
          AND task_blocks.block_type = 'question'
        """
    )
    op.alter_column('task_blocks', 'is_diagnostic', server_default=None)


def downgrade() -> None:
    op.drop_column('task_blocks', 'is_diagnostic')
