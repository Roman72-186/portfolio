"""video quiz questions: от трёх фиксированных полей к произвольному числу

Владелец 29.08.2026 попросил конструктор «плюс — новая строка» вместо трёх
фиксированных вопросов (`a1c7e4f2b809`, 23.08). Нормализует мини-опрос по
образцу анкеты (`survey.py`): вопросы — `learning_video_questions`, ответы на
отдельный вопрос — `video_quiz_answers` (было `video_quiz_responses.answer_1..3`).

Данные переносятся до дропа старых колонок:
- `learning_videos.quiz_question_1..3` → строки `learning_video_questions`,
  порядок и позиция — как было (пустые/NULL пропускаются, остальные по
  порядку полей 1→2→3, `sort_order` с нуля).
- `video_quiz_responses.answer_1..3` → `video_quiz_answers`, сопоставление
  позиционное: `answer_1` — ответ на первый перенесённый (непустой) вопрос
  этого видео, `answer_2` — на второй, и так далее (это ровно то, как
  `app/services/video_quiz.py::save_response` их писал — зипом с уже
  отфильтрованным списком вопросов, а не с сырыми полями). Ответ на позицию,
  для которой вопрос не перенёсся (отвечали, когда вопросов было больше,
  чем сейчас настроено) — считается осиротевшим и пропускается, счётчик
  таких ответов печатается через `print` (alembic глушит logger.info).

Revision ID: 6c1847951c96
Revises: 055b836671da
Create Date: 2026-08-29 16:10:52.774361

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c1847951c96'
down_revision: Union[str, None] = '055b836671da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learning_video_questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "video_id",
            sa.Integer(),
            sa.ForeignKey("learning_videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_learning_video_questions_order",
        "learning_video_questions",
        ["video_id", "sort_order"],
    )
    op.create_table(
        "video_quiz_answers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "response_id",
            sa.Integer(),
            sa.ForeignKey("video_quiz_responses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("learning_video_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "response_id", "question_id", name="uq_video_quiz_answer_response_question"
        ),
    )
    op.create_index(
        "ix_video_quiz_answers_response", "video_quiz_answers", ["response_id"]
    )

    _migrate_data_forward()

    op.drop_column("learning_videos", "quiz_question_3")
    op.drop_column("learning_videos", "quiz_question_2")
    op.drop_column("learning_videos", "quiz_question_1")
    op.drop_column("video_quiz_responses", "answer_3")
    op.drop_column("video_quiz_responses", "answer_2")
    op.drop_column("video_quiz_responses", "answer_1")


def _migrate_data_forward() -> None:
    bind = op.get_bind()

    videos_t = sa.table(
        "learning_videos",
        sa.column("id", sa.Integer()),
        sa.column("quiz_question_1", sa.String()),
        sa.column("quiz_question_2", sa.String()),
        sa.column("quiz_question_3", sa.String()),
    )
    questions_t = sa.table(
        "learning_video_questions",
        sa.column("id", sa.Integer()),
        sa.column("video_id", sa.Integer()),
        sa.column("text", sa.String()),
        sa.column("sort_order", sa.Integer()),
    )
    responses_t = sa.table(
        "video_quiz_responses",
        sa.column("id", sa.Integer()),
        sa.column("video_id", sa.Integer()),
        sa.column("answer_1", sa.Text()),
        sa.column("answer_2", sa.Text()),
        sa.column("answer_3", sa.Text()),
    )
    answers_t = sa.table(
        "video_quiz_answers",
        sa.column("response_id", sa.Integer()),
        sa.column("question_id", sa.Integer()),
        sa.column("text", sa.Text()),
    )

    # video_id → упорядоченный список id перенесённых вопросов (тот же
    # порядок, что видел ученик — непустые quiz_question_1..3 по порядку).
    question_ids_by_video: dict[int, list[int]] = {}
    orphan_answers = 0

    for video_row in bind.execute(sa.select(videos_t)).fetchall():
        texts = [
            value.strip()
            for value in (
                video_row.quiz_question_1,
                video_row.quiz_question_2,
                video_row.quiz_question_3,
            )
            if value and value.strip()
        ]
        if not texts:
            continue
        ids: list[int] = []
        for order, text in enumerate(texts):
            result = bind.execute(
                questions_t.insert()
                .values(video_id=video_row.id, text=text, sort_order=order)
                .returning(questions_t.c.id)
            )
            ids.append(result.scalar_one())
        question_ids_by_video[video_row.id] = ids

    for response_row in bind.execute(sa.select(responses_t)).fetchall():
        question_ids = question_ids_by_video.get(response_row.video_id, [])
        raw_answers = (
            response_row.answer_1,
            response_row.answer_2,
            response_row.answer_3,
        )
        for position, answer_text in enumerate(raw_answers):
            if answer_text is None:
                continue
            if position >= len(question_ids):
                orphan_answers += 1
                continue
            bind.execute(
                answers_t.insert().values(
                    response_id=response_row.id,
                    question_id=question_ids[position],
                    text=answer_text,
                )
            )

    if orphan_answers:
        # alembic глушит logger.info в консоли миграции — print виден в логе
        # `alembic upgrade head` на проде.
        print(
            f"video quiz migration: {orphan_answers} answer(s) had no matching "
            "question after migration and were dropped"
        )


def downgrade() -> None:
    op.add_column(
        "video_quiz_responses",
        sa.Column("answer_1", sa.Text(), nullable=True),
    )
    op.add_column(
        "video_quiz_responses",
        sa.Column("answer_2", sa.Text(), nullable=True),
    )
    op.add_column(
        "video_quiz_responses",
        sa.Column("answer_3", sa.Text(), nullable=True),
    )
    op.add_column(
        "learning_videos",
        sa.Column("quiz_question_1", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "learning_videos",
        sa.Column("quiz_question_2", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "learning_videos",
        sa.Column("quiz_question_3", sa.String(length=300), nullable=True),
    )

    # Возврат лишь частичный: если у видео было больше трёх вопросов, лишние
    # (и ответы на них) при откате теряются — downgrade не symmetric с
    # upgrade, только заглушка под старую схему на случай экстренного отката
    # кода.
    _migrate_data_backward()

    op.drop_index("ix_video_quiz_answers_response", table_name="video_quiz_answers")
    op.drop_table("video_quiz_answers")
    op.drop_index("ix_learning_video_questions_order", table_name="learning_video_questions")
    op.drop_table("learning_video_questions")


def _migrate_data_backward() -> None:
    bind = op.get_bind()

    videos_t = sa.table(
        "learning_videos",
        sa.column("id", sa.Integer()),
        sa.column("quiz_question_1", sa.String()),
        sa.column("quiz_question_2", sa.String()),
        sa.column("quiz_question_3", sa.String()),
    )
    questions_t = sa.table(
        "learning_video_questions",
        sa.column("id", sa.Integer()),
        sa.column("video_id", sa.Integer()),
        sa.column("text", sa.String()),
        sa.column("sort_order", sa.Integer()),
    )
    responses_t = sa.table(
        "video_quiz_responses",
        sa.column("id", sa.Integer()),
        sa.column("video_id", sa.Integer()),
        sa.column("answer_1", sa.Text()),
        sa.column("answer_2", sa.Text()),
        sa.column("answer_3", sa.Text()),
    )
    answers_t = sa.table(
        "video_quiz_answers",
        sa.column("response_id", sa.Integer()),
        sa.column("question_id", sa.Integer()),
        sa.column("text", sa.Text()),
    )

    field_names = ("quiz_question_1", "quiz_question_2", "quiz_question_3")
    answer_field_names = ("answer_1", "answer_2", "answer_3")

    questions_by_video: dict[int, list] = {}
    for row in bind.execute(
        sa.select(questions_t).order_by(questions_t.c.video_id, questions_t.c.sort_order)
    ).fetchall():
        questions_by_video.setdefault(row.video_id, []).append(row)

    for video_id, rows in questions_by_video.items():
        values = {
            field_names[i]: rows[i].text for i in range(min(3, len(rows)))
        }
        if values:
            bind.execute(
                videos_t.update().where(videos_t.c.id == video_id).values(**values)
            )

    for response_row in bind.execute(sa.select(responses_t)).fetchall():
        question_rows = questions_by_video.get(response_row.video_id, [])[:3]
        question_ids = [row.id for row in question_rows]
        if not question_ids:
            continue
        answers_by_question = {
            row.question_id: row.text
            for row in bind.execute(
                sa.select(answers_t).where(
                    answers_t.c.response_id == response_row.id,
                    answers_t.c.question_id.in_(question_ids),
                )
            ).fetchall()
        }
        values = {
            answer_field_names[i]: answers_by_question[question_ids[i]]
            for i in range(len(question_ids))
            if question_ids[i] in answers_by_question
        }
        if values:
            bind.execute(
                responses_t.update()
                .where(responses_t.c.id == response_row.id)
                .values(**values)
            )
