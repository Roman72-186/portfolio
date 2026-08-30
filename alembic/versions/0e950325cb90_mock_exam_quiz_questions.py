"""mock exam quiz questions: мини-опрос после сдачи Пробника, та же
конструкция, что у видео (app/models/mock_exam_quiz.py) — вопросы на
ExamAssignment, ответы ученика отдельной таблицей.

Revision ID: 0e950325cb90
Revises: 6c1847951c96
Create Date: 2026-08-30 13:11:59.068010

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0e950325cb90'
down_revision: Union[str, None] = '6c1847951c96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
    op.create_index(
        "ix_mock_quiz_answers_response", "mock_quiz_answers", ["response_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_mock_quiz_answers_response", table_name="mock_quiz_answers")
    op.drop_table("mock_quiz_answers")
    op.drop_index("ix_mock_quiz_responses_assignment", table_name="mock_quiz_responses")
    op.drop_table("mock_quiz_responses")
    op.drop_index("ix_exam_assignment_questions_order", table_name="exam_assignment_questions")
    op.drop_table("exam_assignment_questions")
