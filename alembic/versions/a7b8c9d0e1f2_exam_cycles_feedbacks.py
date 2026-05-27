"""exam_cycles, feedbacks, feedback_photos + cycle fields on works

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # exam_cycles
    op.create_table(
        'exam_cycles',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('subject', sa.String(50), nullable=False),
        sa.Column('ticket_id', sa.Integer, sa.ForeignKey('exam_tickets.id'), nullable=True),
        sa.Column('started_at', sa.Date, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        'ix_exam_cycles_user_subject_started',
        'exam_cycles',
        ['user_id', 'subject', sa.text('started_at DESC')],
    )

    # works: cycle_id, is_final, parent_work_id, attempt_number
    op.add_column('works', sa.Column('cycle_id', sa.Integer, sa.ForeignKey('exam_cycles.id'), nullable=True))
    op.add_column('works', sa.Column('is_final', sa.Boolean, server_default=sa.text('true'), nullable=False))
    op.add_column('works', sa.Column('parent_work_id', sa.Integer, sa.ForeignKey('works.id'), nullable=True))
    op.add_column('works', sa.Column('attempt_number', sa.Integer, nullable=True))
    op.create_index('ix_works_cycle_id', 'works', ['cycle_id'])
    op.create_index('ix_works_parent_work_id', 'works', ['parent_work_id'])

    # feedbacks (1 на финальную попытку, UNIQUE на work_id)
    op.create_table(
        'feedbacks',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('work_id', sa.Integer, sa.ForeignKey('works.id'), nullable=False, unique=True),
        sa.Column('curator_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('greeting', sa.Text, nullable=True),
        sa.Column('strengths', sa.Text, nullable=True),
        sa.Column('weaknesses', sa.Text, nullable=True),
        sa.Column('recommendations', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # feedback_photos
    op.create_table(
        'feedback_photos',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('feedback_id', sa.Integer, sa.ForeignKey('feedbacks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('s3_path', sa.String(500), nullable=False),
        sa.Column('s3_url', sa.String(500), nullable=False),
        sa.Column('order_idx', sa.Integer, server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_feedback_photos_feedback_id', 'feedback_photos', ['feedback_id'])


def downgrade() -> None:
    op.drop_index('ix_feedback_photos_feedback_id', table_name='feedback_photos')
    op.drop_table('feedback_photos')
    op.drop_table('feedbacks')

    op.drop_index('ix_works_parent_work_id', table_name='works')
    op.drop_index('ix_works_cycle_id', table_name='works')
    op.drop_column('works', 'attempt_number')
    op.drop_column('works', 'parent_work_id')
    op.drop_column('works', 'is_final')
    op.drop_column('works', 'cycle_id')

    op.drop_index('ix_exam_cycles_user_subject_started', table_name='exam_cycles')
    op.drop_table('exam_cycles')
