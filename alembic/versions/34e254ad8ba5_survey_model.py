"""Модель анкеты: surveys, вопросы, варианты, ответы ученика

Revision ID: 34e254ad8ba5
Revises: a1c7e4f2b809
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa


revision = "34e254ad8ba5"
down_revision = "a1c7e4f2b809"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "surveys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_surveys_alive", "surveys", ["deleted_at"])

    op.create_table(
        "survey_questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "survey_id",
            sa.Integer(),
            sa.ForeignKey("surveys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("question_type", sa.String(length=20), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
    )
    op.create_index("ix_survey_questions_survey_id", "survey_questions", ["survey_id"])
    op.create_index(
        "ix_survey_questions_order", "survey_questions", ["survey_id", "sort_order"]
    )

    op.create_table(
        "survey_options",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("survey_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
    )
    op.create_index("ix_survey_options_question_id", "survey_options", ["question_id"])
    op.create_index(
        "ix_survey_options_order", "survey_options", ["question_id", "sort_order"]
    )

    op.create_table(
        "survey_responses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "survey_id",
            sa.Integer(),
            sa.ForeignKey("surveys.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "user_id", name="uq_survey_response_task_user"),
    )
    op.create_index("ix_survey_responses_survey", "survey_responses", ["survey_id"])

    op.create_table(
        "survey_answers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "response_id",
            sa.Integer(),
            sa.ForeignKey("survey_responses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("survey_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "response_id", "question_id", name="uq_survey_answer_response_question"
        ),
    )
    op.create_index("ix_survey_answers_response_id", "survey_answers", ["response_id"])

    op.create_table(
        "survey_answer_options",
        sa.Column(
            "answer_id",
            sa.Integer(),
            sa.ForeignKey("survey_answers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "option_id",
            sa.Integer(),
            sa.ForeignKey("survey_options.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("survey_answer_options")
    op.drop_index("ix_survey_answers_response_id", table_name="survey_answers")
    op.drop_table("survey_answers")
    op.drop_index("ix_survey_responses_survey", table_name="survey_responses")
    op.drop_table("survey_responses")
    op.drop_index("ix_survey_options_order", table_name="survey_options")
    op.drop_index("ix_survey_options_question_id", table_name="survey_options")
    op.drop_table("survey_options")
    op.drop_index("ix_survey_questions_order", table_name="survey_questions")
    op.drop_index("ix_survey_questions_survey_id", table_name="survey_questions")
    op.drop_table("survey_questions")
    op.drop_index("ix_surveys_alive", table_name="surveys")
    op.drop_table("surveys")
