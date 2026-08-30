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
    # `exam_assignments`/`exam_tickets` не заводятся ни одной миграцией в
    # этой цепочке — на проде обе таблицы когда-то создали вручную мимо
    # Alembic, той же исходной формы, что сейчас в моделях (та же история,
    # что и `users.parent_phone` в b2c3d4e5f6a7, найдено 30.08.2026 при
    # попытке поднять чистый docker-compose). На проде уже есть — no-op; на
    # свежей базе — стаб с базовыми колонками, которые сама история миграций
    # никогда не добавляла. Поздние узкие поля (opens_at/closes_at/
    # duration_minutes/target_tag_id — e6f7a8b9c0d1, restrict_start_by_duration
    # — f1a2b3c4d5e6, kind/seq_number/note у assignments — b2f3e4d5c6a7)
    # по-прежнему навешивают их собственные ADD COLUMN, как и раньше.
    op.execute(
        "CREATE TABLE IF NOT EXISTS exam_assignments ("
        "id SERIAL PRIMARY KEY, "
        "title VARCHAR(200) NOT NULL, "
        "subject VARCHAR(50) NOT NULL, "
        "status VARCHAR(20) NOT NULL DEFAULT 'draft', "
        "created_by_id INTEGER NOT NULL REFERENCES users(id), "
        "created_at TIMESTAMPTZ DEFAULT now(), "
        "updated_at TIMESTAMPTZ DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS exam_tickets ("
        "id SERIAL PRIMARY KEY, "
        "assignment_id INTEGER NOT NULL REFERENCES exam_assignments(id), "
        "ticket_number INTEGER NOT NULL, "
        "title VARCHAR(200) NOT NULL, "
        "description TEXT, "
        "image_s3_url VARCHAR(500), "
        "image_s3_path VARCHAR(300), "
        "start_date DATE NOT NULL, "
        "end_date DATE NOT NULL, "
        "assign_to_all BOOLEAN NOT NULL DEFAULT false, "
        "created_at TIMESTAMPTZ DEFAULT now()"
        ")"
    )
    # exam_ticket_assignees — тот же пробел, что exam_tickets/exam_assignments
    # выше, здесь потому что раньше exam_tickets в цепочке не существовало.
    op.execute(
        "CREATE TABLE IF NOT EXISTS exam_ticket_assignees ("
        "id SERIAL PRIMARY KEY, "
        "ticket_id INTEGER NOT NULL REFERENCES exam_tickets(id) ON DELETE CASCADE, "
        "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
        "assigned_at TIMESTAMPTZ DEFAULT now(), "
        "notified_at TIMESTAMPTZ"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_exam_ticket_assignees_ticket_id "
        "ON exam_ticket_assignees (ticket_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_exam_ticket_assignees_user_id "
        "ON exam_ticket_assignees (user_id)"
    )

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
