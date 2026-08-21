"""Учебные программы: служебные темы, теги билета, обложка урока

Три независимых изменения под вкладку «Учебные программы»:

* `learning_topics.kind` разделяет темы недель и служебные темы элементов
  программы, в которых хранится аудитория элемента;
* `exam_ticket_tags` даёт билету несколько тегов — тариф плюс дополнительные;
* обложка урока лежит своей картинкой в S3, потому что thumbnail у Bunny в
  проекте не реализован.

Revision ID: b7f21c93ad40
Revises: a91c4d7e0f22
Create Date: 2026-08-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7f21c93ad40'
down_revision: Union[str, None] = 'a91c4d7e0f22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default оставляем в схеме: он же закрывает уже существующие темы,
    # которые все до одной являются неделями.
    with op.batch_alter_table('learning_topics') as batch:
        batch.add_column(
            sa.Column('kind', sa.String(length=20), nullable=False, server_default='week')
        )
    op.create_index('ix_learning_topics_kind', 'learning_topics', ['kind', 'opens_at'])

    op.create_table(
        'exam_ticket_tags',
        sa.Column('ticket_id', sa.Integer(), nullable=False),
        sa.Column('tag_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['ticket_id'], ['exam_tickets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('ticket_id', 'tag_id'),
    )
    op.create_index('ix_exam_ticket_tags_tag', 'exam_ticket_tags', ['tag_id'])

    with op.batch_alter_table('learning_videos') as batch:
        batch.add_column(sa.Column('cover_s3_url', sa.String(length=500), nullable=True))
        batch.add_column(sa.Column('cover_s3_path', sa.String(length=300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('learning_videos') as batch:
        batch.drop_column('cover_s3_path')
        batch.drop_column('cover_s3_url')

    op.drop_index('ix_exam_ticket_tags_tag', table_name='exam_ticket_tags')
    op.drop_table('exam_ticket_tags')

    op.drop_index('ix_learning_topics_kind', table_name='learning_topics')
    with op.batch_alter_table('learning_topics') as batch:
        batch.drop_column('kind')
