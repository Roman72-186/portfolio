"""Конструктор недели: элементы программы и домашнее задание

Задача трекера получает привязку к неделе (`topic_id` → `learning_topics`),
тип элемента и порядок внутри дня. Домашка заводится отдельной сущностью —
решение владельца от 20.08 по вопросу Р2, не ещё один `kind` у ExamAssignment.

Revision ID: a91c4d7e0f22
Revises: d186f2f1ec72
Create Date: 2026-08-20

Ветвимся от `d186f2f1ec72` (аватар пользователя), а не от своей `7cee183db760`:
соседняя сессия закоммитила миграцию в середине работы, и ответвление от старой
головы дало бы две ветки — `alembic upgrade head` в команде контейнера уронил бы
старт приложения на проде.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a91c4d7e0f22'
down_revision: Union[str, None] = 'd186f2f1ec72'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'homework_assignments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('subject', sa.String(length=50), nullable=True),
        sa.Column('submission_required', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('max_files', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_homework_assignments_alive', 'homework_assignments', ['deleted_at'])

    op.create_table(
        'homework_images',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('homework_id', sa.Integer(), nullable=False),
        sa.Column('image_s3_url', sa.String(length=500), nullable=False),
        sa.Column('image_s3_path', sa.String(length=300), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['homework_id'], ['homework_assignments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_homework_images_homework_id', 'homework_images', ['homework_id'])
    op.create_index('ix_homework_images_order', 'homework_images', ['homework_id', 'sort_order'])

    # Три колонки в существующую таблицу задач. Значения по умолчанию заданы на
    # уровне сервера: таблица на проде пустая, но правило то же — колонка
    # NOT NULL без server_default уронила бы миграцию на непустой таблице.
    with op.batch_alter_table('tracker_tasks') as batch:
        batch.add_column(sa.Column('topic_id', sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column('kind', sa.String(length=20), nullable=False, server_default='other')
        )
        batch.add_column(
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0')
        )
        batch.create_foreign_key(
            'fk_tracker_tasks_topic', 'learning_topics', ['topic_id'], ['id'],
            ondelete='SET NULL',
        )
    op.create_index(
        'ix_tracker_tasks_topic', 'tracker_tasks', ['topic_id', 'due_at', 'sort_order']
    )


def downgrade() -> None:
    op.drop_index('ix_tracker_tasks_topic', table_name='tracker_tasks')
    with op.batch_alter_table('tracker_tasks') as batch:
        batch.drop_constraint('fk_tracker_tasks_topic', type_='foreignkey')
        batch.drop_column('sort_order')
        batch.drop_column('kind')
        batch.drop_column('topic_id')

    op.drop_index('ix_homework_images_order', table_name='homework_images')
    op.drop_index('ix_homework_images_homework_id', table_name='homework_images')
    op.drop_table('homework_images')
    op.drop_index('ix_homework_assignments_alive', table_name='homework_assignments')
    op.drop_table('homework_assignments')
