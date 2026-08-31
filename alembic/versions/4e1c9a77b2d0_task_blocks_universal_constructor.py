"""task blocks: универсальный конструктор содержимого элемента дня.

Владелец 31.08.2026: любой вид `TrackerTask.kind` должен уметь нести что
угодно — текст, фото, видео из уже загруженных, ссылку, вопрос — в любом
порядке, а не один зашитый вид содержимого на тип элемента. `kind` при этом
сохраняет прежний смысл «в какую вкладку недели попадёт карточка».

Мини-опрос `task_quiz_*` (заведён 30.08 миграцией `841212ccdafd`) переезжает
сюда целиком: два похожих места для вопросов в одной форме преподавателя
путали бы больше, чем экономили. Отдельно остаются `survey_*` (шаблон анкеты
на восемь точек года) и `video_quiz_*` (привязан к ролику, живущему вне
одного дня программы).

Данные копируются, не отбрасываются. Прямо перенести id вопросов нельзя —
`task_blocks` общий стол для пяти типов блоков, id вопроса перестал быть
уникальным ключом строки, - поэтому на время переноса заводится временная
колонка `legacy_quiz_question_id`, по ней перекладываются ответы, и она
удаляется в конце. После explicit-id INSERT у Postgres нужно подвинуть
sequence вручную.

Revision ID: 4e1c9a77b2d0
Revises: 23b327018d61
Create Date: 2026-08-31 12:10:44.183920

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4e1c9a77b2d0'
down_revision: Union[str, None] = '23b327018d61'
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
        "task_blocks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tracker_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("block_type", sa.String(length=20), nullable=False, server_default="text"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "video_id",
            sa.Integer(),
            sa.ForeignKey("learning_videos.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("image_s3_url", sa.String(length=500), nullable=True),
        sa.Column("image_s3_path", sa.String(length=300), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("question_type", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # Временная: живёт только внутри этой миграции, нужна чтобы разложить
        # ответы учеников по новым блокам. Удаляется ниже, до конца upgrade().
        sa.Column("legacy_quiz_question_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_task_blocks_order", "task_blocks", ["task_id", "sort_order"])
    op.create_index("ix_task_blocks_video", "task_blocks", ["video_id"])

    op.create_table(
        "task_block_options",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "block_id",
            sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_task_block_options_block", "task_block_options", ["block_id"])
    op.create_index(
        "ix_task_block_options_order", "task_block_options", ["block_id", "sort_order"]
    )

    op.create_table(
        "task_block_responses",
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
        sa.UniqueConstraint("task_id", "user_id", name="uq_task_block_response_task_user"),
    )
    op.create_index("ix_task_block_responses_task", "task_block_responses", ["task_id"])

    op.create_table(
        "task_block_answers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "response_id",
            sa.Integer(),
            sa.ForeignKey("task_block_responses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "block_id",
            sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "response_id", "block_id", name="uq_task_block_answer_response_block"
        ),
    )
    op.create_index("ix_task_block_answers_response", "task_block_answers", ["response_id"])

    op.create_table(
        "task_block_answer_options",
        sa.Column(
            "answer_id",
            sa.Integer(),
            sa.ForeignKey("task_block_answers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "option_id",
            sa.Integer(),
            sa.ForeignKey("task_block_options.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # Перенос мини-опроса. Вопрос становится блоком «вопрос» со свободным
    # текстом — ровно тем, чем он и был: вариантов ответа у task_quiz не было.
    op.execute(
        """
        INSERT INTO task_blocks (
            task_id, block_type, sort_order, body, question_type,
            created_at, updated_at, legacy_quiz_question_id
        )
        SELECT task_id, 'question', sort_order, text, 'text',
               NOW(), NOW(), id
        FROM task_quiz_questions
        """
    )
    op.execute(
        """
        INSERT INTO task_block_responses (id, task_id, user_id, created_at, updated_at)
        SELECT id, task_id, user_id, created_at, updated_at FROM task_quiz_responses
        """
    )
    op.execute(
        """
        INSERT INTO task_block_answers (id, response_id, block_id, text)
        SELECT tqa.id, tqa.response_id, tb.id, tqa.text
        FROM task_quiz_answers tqa
        JOIN task_blocks tb ON tb.legacy_quiz_question_id = tqa.question_id
        """
    )
    _bump_sequence("task_blocks")
    _bump_sequence("task_block_responses")
    _bump_sequence("task_block_answers")

    op.drop_column("task_blocks", "legacy_quiz_question_id")

    op.drop_index("ix_task_quiz_answers_response", table_name="task_quiz_answers")
    op.drop_table("task_quiz_answers")
    op.drop_index("ix_task_quiz_responses_task", table_name="task_quiz_responses")
    op.drop_table("task_quiz_responses")
    op.drop_index("ix_task_quiz_questions_order", table_name="task_quiz_questions")
    op.drop_table("task_quiz_questions")


def downgrade() -> None:
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

    # Обратно уезжают только блоки-вопросы со свободным текстом — единственное,
    # что мини-опрос умел выразить. Текст, фото, видео, ссылки и вопросы с
    # вариантами в старой схеме места не имеют и остаются в удаляемых таблицах:
    # откат этой миграции — потеря такого содержимого, и это осознанно.
    op.execute(
        """
        INSERT INTO task_quiz_questions (id, task_id, text, sort_order)
        SELECT id, task_id, LEFT(COALESCE(body, ''), 300), sort_order
        FROM task_blocks
        WHERE block_type = 'question' AND question_type = 'text'
        """
    )
    op.execute(
        """
        INSERT INTO task_quiz_responses (id, task_id, user_id, created_at, updated_at)
        SELECT id, task_id, user_id, created_at, updated_at FROM task_block_responses
        """
    )
    op.execute(
        """
        INSERT INTO task_quiz_answers (id, response_id, question_id, text)
        SELECT tba.id, tba.response_id, tba.block_id, tba.text
        FROM task_block_answers tba
        JOIN task_quiz_questions tqq ON tqq.id = tba.block_id
        """
    )
    _bump_sequence("task_quiz_questions")
    _bump_sequence("task_quiz_responses")
    _bump_sequence("task_quiz_answers")

    op.drop_table("task_block_answer_options")
    op.drop_index("ix_task_block_answers_response", table_name="task_block_answers")
    op.drop_table("task_block_answers")
    op.drop_index("ix_task_block_responses_task", table_name="task_block_responses")
    op.drop_table("task_block_responses")
    op.drop_index("ix_task_block_options_order", table_name="task_block_options")
    op.drop_index("ix_task_block_options_block", table_name="task_block_options")
    op.drop_table("task_block_options")
    op.drop_index("ix_task_blocks_video", table_name="task_blocks")
    op.drop_index("ix_task_blocks_order", table_name="task_blocks")
    op.drop_table("task_blocks")
