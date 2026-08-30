"""task quiz generic table: мини-опрос переезжает с отдельных таблиц под
каждый вид (`exam_assignment_questions`/`mock_quiz_responses`/
`mock_quiz_answers`, заведены только что в `0e950325cb90`) на один общий
стол, ключ — `task_id` (владелец 30.08.2026, см. `app/models/task_quiz.py`):
мини-опрос теперь доступен всем восьми видам `TrackerTask.kind`, а не
только Пробнику. Видео остаётся на своих таблицах (`video_quiz.py`) — там
уже есть реальные ответы учеников, трогать не нужно.

Данные копируются, не отбрасываются: строки `exam_assignment_questions`
переносятся под `task_id` того `TrackerTask`, что создан вместе с этим
`ExamAssignment` (`source_kind='exam_assignment'`), id вопросов и ответов
сохраняются как есть, чтобы FK `mock_quiz_answers.question_id`/
`.response_id` перекопировались один в один без пересборки связей. После
копирования у Postgres нужно подвинуть sequence вручную — explicit-id
INSERT её не двигает.

Revision ID: 841212ccdafd
Revises: 0e950325cb90
Create Date: 2026-08-30 14:46:30.760725

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '841212ccdafd'
down_revision: Union[str, None] = '0e950325cb90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bump_sequence(table: str, column: str = "id") -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), "
        f"COALESCE((SELECT MAX({column}) FROM {table}), 1), "
        f"(SELECT MAX({column}) FROM {table}) IS NOT NULL)"
    )


def upgrade() -> None:
    op.create_table(
        "task_quiz_questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tracker_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_task_quiz_questions_order", "task_quiz_questions", ["task_id", "sort_order"]
    )
    op.create_table(
        "task_quiz_responses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tracker_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "user_id", name="uq_task_quiz_response_task_user"),
    )
    op.create_index("ix_task_quiz_responses_task", "task_quiz_responses", ["task_id"])
    op.create_table(
        "task_quiz_answers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "response_id",
            sa.Integer(),
            sa.ForeignKey("task_quiz_responses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("task_quiz_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "response_id", "question_id", name="uq_task_quiz_answer_response_question"
        ),
    )
    op.create_index("ix_task_quiz_answers_response", "task_quiz_answers", ["response_id"])

    op.execute(
        """
        INSERT INTO task_quiz_questions (id, task_id, text, sort_order)
        SELECT eaq.id, tt.id, eaq.text, eaq.sort_order
        FROM exam_assignment_questions eaq
        JOIN tracker_tasks tt
          ON tt.source_kind = 'exam_assignment' AND tt.source_id = eaq.assignment_id
        """
    )
    op.execute(
        """
        INSERT INTO task_quiz_responses (id, task_id, user_id, created_at, updated_at)
        SELECT mqr.id, tt.id, mqr.user_id, mqr.created_at, mqr.updated_at
        FROM mock_quiz_responses mqr
        JOIN tracker_tasks tt
          ON tt.source_kind = 'exam_assignment' AND tt.source_id = mqr.assignment_id
        """
    )
    op.execute(
        """
        INSERT INTO task_quiz_answers (id, response_id, question_id, text)
        SELECT id, response_id, question_id, text FROM mock_quiz_answers
        """
    )
    _bump_sequence("task_quiz_questions")
    _bump_sequence("task_quiz_responses")
    _bump_sequence("task_quiz_answers")

    op.drop_index("ix_mock_quiz_answers_response", table_name="mock_quiz_answers")
    op.drop_table("mock_quiz_answers")
    op.drop_index("ix_mock_quiz_responses_assignment", table_name="mock_quiz_responses")
    op.drop_table("mock_quiz_responses")
    op.drop_index("ix_exam_assignment_questions_order", table_name="exam_assignment_questions")
    op.drop_table("exam_assignment_questions")


def downgrade() -> None:
    op.create_table(
        "exam_assignment_questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "assignment_id",
            sa.Integer(),
            sa.ForeignKey("exam_assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_exam_assignment_questions_order",
        "exam_assignment_questions",
        ["assignment_id", "sort_order"],
    )
    op.create_table(
        "mock_quiz_responses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "assignment_id",
            sa.Integer(),
            sa.ForeignKey("exam_assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "assignment_id", "user_id", name="uq_mock_quiz_response_assignment_user"
        ),
    )
    op.create_index(
        "ix_mock_quiz_responses_assignment", "mock_quiz_responses", ["assignment_id"]
    )
    op.create_table(
        "mock_quiz_answers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "response_id",
            sa.Integer(),
            sa.ForeignKey("mock_quiz_responses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("exam_assignment_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "response_id", "question_id", name="uq_mock_quiz_answer_response_question"
        ),
    )
    op.create_index("ix_mock_quiz_answers_response", "mock_quiz_answers", ["response_id"])

    op.execute(
        """
        INSERT INTO exam_assignment_questions (id, assignment_id, text, sort_order)
        SELECT tqq.id, tt.source_id, tqq.text, tqq.sort_order
        FROM task_quiz_questions tqq
        JOIN tracker_tasks tt ON tt.id = tqq.task_id AND tt.source_kind = 'exam_assignment'
        """
    )
    op.execute(
        """
        INSERT INTO mock_quiz_responses (id, assignment_id, user_id, created_at, updated_at)
        SELECT tqr.id, tt.source_id, tqr.user_id, tqr.created_at, tqr.updated_at
        FROM task_quiz_responses tqr
        JOIN tracker_tasks tt ON tt.id = tqr.task_id AND tt.source_kind = 'exam_assignment'
        """
    )
    op.execute(
        """
        INSERT INTO mock_quiz_answers (id, response_id, question_id, text)
        SELECT id, response_id, question_id, text FROM task_quiz_answers
        """
    )
    _bump_sequence("exam_assignment_questions")
    _bump_sequence("mock_quiz_responses")
    _bump_sequence("mock_quiz_answers")

    op.drop_index("ix_task_quiz_answers_response", table_name="task_quiz_answers")
    op.drop_table("task_quiz_answers")
    op.drop_index("ix_task_quiz_responses_task", table_name="task_quiz_responses")
    op.drop_table("task_quiz_responses")
    op.drop_index("ix_task_quiz_questions_order", table_name="task_quiz_questions")
    op.drop_table("task_quiz_questions")
