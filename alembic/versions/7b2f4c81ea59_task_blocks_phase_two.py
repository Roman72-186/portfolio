"""task blocks, вторая очередь: скрытые вопросы, галерея, «просмотрено»,
перенос мини-опроса видео и анкеты в блоки.

Решения владельца 31.08.2026, всё схемное собрано в одну миграцию, чтобы на
прод уехал один файл, а не четыре:

1. `task_blocks.hidden_until_done` — вопрос показывается только после того, как
   ученик закрыл задание. В проверке «ответил ли на все вопросы» такие вопросы
   не участвуют, иначе выходил бы тупик: вопрос не виден, ответить нельзя,
   задание не закрыть, неделя встала.
2. `task_block_images` — блок «фото» стал галереей до десяти снимков. Прежние
   колонки `image_s3_url`/`image_s3_path` переезжают туда по одной строке на
   блок и удаляются.
3. `task_block_answers.reviewed_at`/`reviewed_by_id` — отметка «просмотрено»
   для очереди проверки. Ставит и снимает только staff.
4. Индекс по `user_id` у `task_block_responses` — под экран «ответы одного
   ученика», который делаем следующим.
5. Мини-опрос видео (`video_quiz_*`) и анкета (`survey_*`) переезжают в блоки
   вместе с ответами учеников. После этого вопросы у преподавателя живут в
   одном месте, а не в трёх.

**Про пункт 5 и то, чего он стоит.** Вопросы перестают принадлежать ролику и
анкете-шаблону и начинают принадлежать заданию. Для существующих данных перенос
однозначен: до конструктора привязка ролика жила в единственной колонке
`learning_videos.topic_id`, то есть ролик стоит максимум в одном задании.
Вперёд поведение меняется — тот же ролик в другом задании придёт без вопросов,
их заводят заново (владелец подтвердил 31.08). Заготовок анкет, не поставленных
ни в один день, у владельца нет — такие строки просто удаляются вместе со
старыми таблицами.

**Откат восстанавливает только структуру старых таблиц, не содержимое.** После
удаления временных колонок связь «этот блок пришёл из анкеты» не
восстанавливается ничем. Откат рассчитан на сценарий «миграция сломала прод,
откатываемся в ближайшие минуты», когда новых блоков ещё не появилось. Если
откат понадобится позже — восстанавливать из бэкапа базы, а не этой функцией.

Revision ID: 7b2f4c81ea59
Revises: 4e1c9a77b2d0
Create Date: 2026-08-31 15:42:10.554120

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b2f4c81ea59'
down_revision: Union[str, None] = '4e1c9a77b2d0'
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
    # ── 1. Скрытые до сдачи вопросы ──────────────────────────────────────────
    op.add_column(
        "task_blocks",
        sa.Column(
            "hidden_until_done", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # ── 2. Галерея картинок ──────────────────────────────────────────────────
    op.create_table(
        "task_block_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("image_s3_url", sa.String(length=500), nullable=False),
        sa.Column("image_s3_path", sa.String(length=300), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_task_block_images_block", "task_block_images", ["block_id"])
    op.create_index(
        "ix_task_block_images_order", "task_block_images", ["block_id", "sort_order"]
    )
    op.execute(
        """
        INSERT INTO task_block_images (block_id, image_s3_url, image_s3_path, sort_order)
        SELECT id, image_s3_url, image_s3_path, 0
        FROM task_blocks
        WHERE image_s3_url IS NOT NULL AND image_s3_url <> ''
        """
    )
    op.drop_column("task_blocks", "image_s3_url")
    op.drop_column("task_blocks", "image_s3_path")

    # ── 3. Отметка «просмотрено» ─────────────────────────────────────────────
    op.add_column(
        "task_block_answers",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "task_block_answers",
        sa.Column(
            "reviewed_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_index(
        "ix_task_block_answers_reviewed", "task_block_answers", ["reviewed_at"]
    )

    # ── 4. Индекс под экран «по ученику» ─────────────────────────────────────
    op.create_index(
        "ix_task_block_responses_user", "task_block_responses", ["user_id"]
    )

    # ── 5. Перенос мини-опроса видео и анкеты ────────────────────────────────
    # Временные колонки живут только внутри этой миграции: по ним ответы
    # раскладываются по новым блокам. Удаляются ниже, до конца upgrade().
    op.add_column("task_blocks", sa.Column("legacy_vq_id", sa.Integer(), nullable=True))
    op.add_column("task_blocks", sa.Column("legacy_sq_id", sa.Integer(), nullable=True))
    op.add_column(
        "task_block_options", sa.Column("legacy_so_id", sa.Integer(), nullable=True)
    )

    # 5а. Вопросы мини-опроса видео → блоки задания, где стоит этот ролик.
    # Ставим после уже существующих блоков задания: нумерация продолжается с
    # максимума, а не затирает порядок, который расставил преподаватель.
    op.execute(
        """
        INSERT INTO task_blocks (
            task_id, block_type, question_type, body, sort_order,
            hidden_until_done, created_at, updated_at, legacy_vq_id
        )
        SELECT src.task_id, 'question', 'text', src.text,
               src.base + src.rn, false, NOW(), NOW(), src.qid
        FROM (
            SELECT tt.id AS task_id,
                   lvq.text AS text,
                   lvq.id AS qid,
                   COALESCE((
                       SELECT MAX(tb.sort_order) + 1 FROM task_blocks tb
                       WHERE tb.task_id = tt.id
                   ), 0) AS base,
                   ROW_NUMBER() OVER (
                       PARTITION BY tt.id ORDER BY lvq.sort_order, lvq.id
                   ) - 1 AS rn
            FROM learning_video_questions lvq
            JOIN learning_videos lv ON lv.id = lvq.video_id
            JOIN tracker_tasks tt
              ON tt.topic_id = lv.topic_id AND tt.kind = 'video'
                 AND tt.deleted_at IS NULL
            WHERE lv.deleted_at IS NULL AND lv.topic_id IS NOT NULL
        ) src
        """
    )

    # 5б. Вопросы анкеты → блоки каждого задания, где эта анкета стоит.
    op.execute(
        """
        INSERT INTO task_blocks (
            task_id, block_type, question_type, body, sort_order,
            hidden_until_done, created_at, updated_at, legacy_sq_id
        )
        SELECT src.task_id, 'question', src.question_type, LEFT(src.text, 5000),
               src.base + src.rn, false, NOW(), NOW(), src.qid
        FROM (
            SELECT tt.id AS task_id,
                   sq.text AS text,
                   sq.question_type AS question_type,
                   sq.id AS qid,
                   COALESCE((
                       SELECT MAX(tb.sort_order) + 1 FROM task_blocks tb
                       WHERE tb.task_id = tt.id
                   ), 0) AS base,
                   ROW_NUMBER() OVER (
                       PARTITION BY tt.id ORDER BY sq.sort_order, sq.id
                   ) - 1 AS rn
            FROM survey_questions sq
            JOIN tracker_tasks tt
              ON tt.source_kind = 'survey' AND tt.source_id = sq.survey_id
                 AND tt.deleted_at IS NULL
        ) src
        """
    )

    # 5в. Варианты ответа анкеты.
    op.execute(
        """
        INSERT INTO task_block_options (block_id, text, is_correct, sort_order, legacy_so_id)
        SELECT tb.id, so.text, so.is_correct, so.sort_order, so.id
        FROM survey_options so
        JOIN task_blocks tb ON tb.legacy_sq_id = so.question_id
        """
    )

    # 5г. Заполнения. Строка `task_block_responses` могла уже существовать —
    # ученик отвечал на блоки того же задания. Тогда переиспользуем её, иначе
    # уникальность (task_id, user_id) не пустила бы вторую.
    op.execute(
        """
        INSERT INTO task_block_responses (task_id, user_id, created_at, updated_at)
        SELECT DISTINCT tt.id, vqr.user_id, vqr.created_at, vqr.updated_at
        FROM video_quiz_responses vqr
        JOIN learning_videos lv ON lv.id = vqr.video_id
        JOIN tracker_tasks tt
          ON tt.topic_id = lv.topic_id AND tt.kind = 'video' AND tt.deleted_at IS NULL
        WHERE lv.topic_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM task_block_responses r
              WHERE r.task_id = tt.id AND r.user_id = vqr.user_id
          )
        """
    )
    op.execute(
        """
        INSERT INTO task_block_responses (task_id, user_id, created_at, updated_at)
        SELECT DISTINCT sr.task_id, sr.user_id, sr.submitted_at, sr.submitted_at
        FROM survey_responses sr
        WHERE NOT EXISTS (
            SELECT 1 FROM task_block_responses r
            WHERE r.task_id = sr.task_id AND r.user_id = sr.user_id
        )
        """
    )

    # 5д. Ответы. Связь со строкой заполнения — по паре (task_id, user_id).
    op.execute(
        """
        INSERT INTO task_block_answers (response_id, block_id, text)
        SELECT r.id, tb.id, vqa.text
        FROM video_quiz_answers vqa
        JOIN video_quiz_responses vqr ON vqr.id = vqa.response_id
        JOIN task_blocks tb ON tb.legacy_vq_id = vqa.question_id
        JOIN task_block_responses r
          ON r.task_id = tb.task_id AND r.user_id = vqr.user_id
        ON CONFLICT (response_id, block_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO task_block_answers (response_id, block_id, text)
        SELECT r.id, tb.id, sa.text
        FROM survey_answers sa
        JOIN survey_responses sr ON sr.id = sa.response_id
        JOIN task_blocks tb
          ON tb.legacy_sq_id = sa.question_id AND tb.task_id = sr.task_id
        JOIN task_block_responses r
          ON r.task_id = sr.task_id AND r.user_id = sr.user_id
        ON CONFLICT (response_id, block_id) DO NOTHING
        """
    )

    # 5е. Выбранные варианты анкеты.
    op.execute(
        """
        INSERT INTO task_block_answer_options (answer_id, option_id)
        SELECT tba.id, tbo.id
        FROM survey_answer_options sao
        JOIN survey_answers sa ON sa.id = sao.answer_id
        JOIN survey_responses sr ON sr.id = sa.response_id
        JOIN task_blocks tb
          ON tb.legacy_sq_id = sa.question_id AND tb.task_id = sr.task_id
        JOIN task_block_options tbo
          ON tbo.legacy_so_id = sao.option_id AND tbo.block_id = tb.id
        JOIN task_block_responses r
          ON r.task_id = sr.task_id AND r.user_id = sr.user_id
        JOIN task_block_answers tba
          ON tba.response_id = r.id AND tba.block_id = tb.id
        ON CONFLICT (answer_id, option_id) DO NOTHING
        """
    )

    _bump_sequence("task_blocks")
    _bump_sequence("task_block_options")
    _bump_sequence("task_block_images")
    _bump_sequence("task_block_responses")
    _bump_sequence("task_block_answers")

    op.drop_column("task_blocks", "legacy_vq_id")
    op.drop_column("task_blocks", "legacy_sq_id")
    op.drop_column("task_block_options", "legacy_so_id")

    # Источник «анкета» исчезает вместе с сущностью: `source_id` указывал бы на
    # удалённую строку, а гасить задачу по факту заполнения анкеты больше
    # некому — теперь это обычные блоки-вопросы.
    op.execute(
        "UPDATE tracker_tasks SET source_kind = NULL, source_id = NULL "
        "WHERE source_kind = 'survey'"
    )

    # Старые таблицы сносим в порядке зависимостей.
    op.drop_table("survey_answer_options")
    op.drop_table("survey_answers")
    op.drop_table("survey_responses")
    op.drop_table("survey_options")
    op.drop_table("survey_questions")
    op.drop_table("surveys")
    op.drop_table("video_quiz_answers")
    op.drop_table("video_quiz_responses")
    op.drop_table("learning_video_questions")


def downgrade() -> None:
    """Восстанавливает структуру старых таблиц, но не содержимое — см. шапку."""
    op.create_table(
        "learning_video_questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "video_id", sa.Integer(),
            sa.ForeignKey("learning_videos.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_learning_video_questions_order",
        "learning_video_questions", ["video_id", "sort_order"],
    )
    op.create_table(
        "video_quiz_responses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "video_id", sa.Integer(),
            sa.ForeignKey("learning_videos.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("video_id", "user_id", name="uq_video_quiz_response_video_user"),
    )
    op.create_index("ix_video_quiz_responses_video", "video_quiz_responses", ["video_id"])
    op.create_table(
        "video_quiz_answers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "response_id", sa.Integer(),
            sa.ForeignKey("video_quiz_responses.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "question_id", sa.Integer(),
            sa.ForeignKey("learning_video_questions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "response_id", "question_id", name="uq_video_quiz_answer_response_question"
        ),
    )
    op.create_index("ix_video_quiz_answers_response", "video_quiz_answers", ["response_id"])

    op.create_table(
        "surveys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "created_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
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
            "survey_id", sa.Integer(),
            sa.ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("question_type", sa.String(length=20), nullable=False, server_default="text"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_survey_questions_survey_id", "survey_questions", ["survey_id"])
    op.create_index(
        "ix_survey_questions_order", "survey_questions", ["survey_id", "sort_order"]
    )
    op.create_table(
        "survey_options",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "question_id", sa.Integer(),
            sa.ForeignKey("survey_questions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_survey_options_question_id", "survey_options", ["question_id"])
    op.create_index("ix_survey_options_order", "survey_options", ["question_id", "sort_order"])
    op.create_table(
        "survey_responses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "survey_id", sa.Integer(),
            sa.ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "task_id", sa.Integer(),
            sa.ForeignKey("tracker_tasks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "user_id", name="uq_survey_response_task_user"),
    )
    op.create_index("ix_survey_responses_survey", "survey_responses", ["survey_id"])
    op.create_index("ix_survey_responses_survey_id", "survey_responses", ["survey_id"])
    op.create_table(
        "survey_answers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "response_id", sa.Integer(),
            sa.ForeignKey("survey_responses.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "question_id", sa.Integer(),
            sa.ForeignKey("survey_questions.id", ondelete="CASCADE"), nullable=False,
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
            "answer_id", sa.Integer(),
            sa.ForeignKey("survey_answers.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "option_id", sa.Integer(),
            sa.ForeignKey("survey_options.id", ondelete="CASCADE"), primary_key=True,
        ),
    )

    op.drop_index("ix_task_block_responses_user", table_name="task_block_responses")
    op.drop_index("ix_task_block_answers_reviewed", table_name="task_block_answers")
    op.drop_column("task_block_answers", "reviewed_by_id")
    op.drop_column("task_block_answers", "reviewed_at")

    op.add_column("task_blocks", sa.Column("image_s3_url", sa.String(length=500), nullable=True))
    op.add_column("task_blocks", sa.Column("image_s3_path", sa.String(length=300), nullable=True))
    op.execute(
        """
        UPDATE task_blocks tb
        SET image_s3_url = i.image_s3_url, image_s3_path = i.image_s3_path
        FROM (
            SELECT DISTINCT ON (block_id) block_id, image_s3_url, image_s3_path
            FROM task_block_images ORDER BY block_id, sort_order, id
        ) i
        WHERE i.block_id = tb.id
        """
    )
    op.drop_index("ix_task_block_images_order", table_name="task_block_images")
    op.drop_index("ix_task_block_images_block", table_name="task_block_images")
    op.drop_table("task_block_images")

    op.drop_column("task_blocks", "hidden_until_done")
