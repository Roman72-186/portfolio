"""Универсальный конструктор содержимого элемента дня (владелец 31.08.2026).

**Добавка 05.09.2026 — единая лента блоков для предобучения.** Созвон
владельца с методистом 03.09.2026 попросил блокировку и обязательность на
уровне отдельного блока внутри одной ленты (не только на уровне вкладки,
как решили 23.08 — `plans/2026-08-23-apparchi-constructor-and-tracker-open-
questions.md`). Три новых поля здесь и две новые таблицы ниже — под это:
`is_required`/`subject` у блока, `TaskBlockTariff` (per-блок тарифный гейт,
зеркало `TrackerTaskTag`) и `TaskBlockState` (статус блока у ученика, зеркало
`TrackerTaskState` — `TaskBlockResponse` для этого не годится, она про
заполнение вопросов всего задания разом, а не про состояние одного блока).
Подробности и открытые вопросы — `plans/2026-09-04-apparchi-precourse-block-
feed-implementation-plan.md`. Роуты/шаблоны единой ленты в этой стройке ещё
не собраны — только модель и сервисный слой.

До этой модели каждый вид `TrackerTask.kind` умел ровно один вид содержимого:
«Видеоматериал» — только ролик, «Самостоятельная работа» — только картинки,
а «Материал»/«Тест по теории»/«Занятие»/«Чек-лист» вообще ничего, кроме
заголовка и описания. Преподаватель подстраивался под форму.

Теперь содержимое любого элемента — свободный список блоков: текст, фото,
видео из уже загруженных, ссылка, вопрос. Порядок задаёт `sort_order`, набор
типов ограничен только `BLOCK_TYPES`.

**`kind` при этом не меняет смысла на «что можно положить».** Он по-прежнему
отвечает ровно на один вопрос: в какую из восьми вкладок недели попадёт
карточка (`WEEK_TAB_SEQUENCE` в `app/models/tracker.py`). Гейт «блок → неделя
→ месяц» и порядок вкладок эта модель не трогает.

Блоки — это содержимое, они **не гасят задачу**. Задача закрывается как и
раньше: галочкой ученика или событием-источником (`source_kind`). Отметка
каждого блока отдельно — отдельная будущая стройка (решение владельца 31.08).

Сюда переехал мини-опрос `task_quiz_*` (был заведён 30.08) вместе с ответами
учеников: два похожих места для вопросов в одной форме преподавателя путали
бы больше, чем экономили. Отдельно остаются `survey_*` — переиспользуемый
шаблон анкеты на восемь точек года, и `video_quiz_*` — мини-опрос привязан к
ролику, который живёт вне одного дня программы.

Типы вопроса и семантику `is_correct` не изобретаем заново — берём готовые
из `app/models/survey.py`, чтобы у преподавателя был один язык в обеих формах.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.tracker import STATUS_OPEN

# Типы вопроса. Раньше жили в `app/models/survey.py` и импортировались сюда;
# после переезда анкеты в блоки (31.08.2026) анкеты как отдельной сущности нет,
# и это единственный их дом.
QUESTION_SINGLE = "single"      # один вариант ответа, один верный (викторина)
QUESTION_MULTIPLE = "multiple"  # несколько вариантов, два и более верных
QUESTION_TEXT = "text"          # свободный текст, без вариантов

QUESTION_TYPES = (QUESTION_SINGLE, QUESTION_MULTIPLE, QUESTION_TEXT)

QUESTION_TYPE_LABELS = {
    QUESTION_SINGLE: "Один вариант ответа",
    QUESTION_MULTIPLE: "Несколько вариантов ответа",
    QUESTION_TEXT: "Свободный текст",
}

BLOCK_TEXT = "text"          # абзац текста
BLOCK_PHOTO = "photo"        # одна картинка в S3
BLOCK_VIDEO = "video"        # ролик из уже загруженных (LearningVideo)
BLOCK_LINK = "link"          # ссылка, рисуется кнопкой
BLOCK_QUESTION = "question"  # вопрос с вариантами ответа или свободным текстом

# Новый тип — строка здесь плюс ветка в шаблоне-рендере, миграция не нужна:
# специализированные колонки уже nullable, общего JSON-поля намеренно нет
# (в проекте нет ни одного JSONB, все списки — нормализованные таблицы).
BLOCK_TYPES = (BLOCK_TEXT, BLOCK_PHOTO, BLOCK_VIDEO, BLOCK_LINK, BLOCK_QUESTION)

BLOCK_TYPE_LABELS = {
    BLOCK_TEXT: "Текст",
    BLOCK_PHOTO: "Фото",
    BLOCK_VIDEO: "Видео",
    BLOCK_LINK: "Ссылка",
    BLOCK_QUESTION: "Вопрос",
}

# Тот же потолок, что у мини-опроса видео и прежнего task_quiz — общий язык
# конструктора, не повод для отдельной константы.
MAX_BLOCKS = 50

# Сколько картинок влезает в один блок-галерею (владелец 31.08.2026).
MAX_BLOCK_IMAGES = 10


class TaskBlock(Base):
    """Один блок содержимого элемента дня.

    Специализированные колонки (`video_id`, `image_s3_*`, `url`,
    `question_type`) заполняются только под свой тип и nullable у остальных:
    полиморфных таблиц вложений в проекте нет, каждая сущность несёт свои
    колонки — держимся этой же конвенции.
    """

    __tablename__ = "task_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_tasks.id", ondelete="CASCADE"), nullable=False
    )
    block_type: Mapped[str] = mapped_column(String(20), nullable=False, default=BLOCK_TEXT)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Подпись блока — необязательна у всех типов: у фото это подрисуночная
    # строка, у ссылки — надпись на кнопке, у видео — заголовок над плеером.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Текст блока: сам абзац у text, текст вопроса у question.
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # SET NULL, а не CASCADE: удалённый ролик не должен уносить с собой
    # соседние блоки и ответы учеников на вопросы того же элемента.
    video_id: Mapped[int | None] = mapped_column(
        ForeignKey("learning_videos.id", ondelete="SET NULL"), nullable=True
    )
    # Картинки блока — в отдельной таблице `TaskBlockImage`: блок «фото» стал
    # галереей до MAX_BLOCK_IMAGES снимков (владелец 31.08.2026). Раньше пара
    # колонок url+path лежала прямо здесь, по одной картинке на блок.
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    question_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Показывать вопрос только после того, как ученик закрыл задание
    # (владелец 31.08.2026). Скрытый вопрос — рефлексия по факту сдачи, поэтому
    # он **не участвует** в проверке «ответил ли на все вопросы»: иначе выходил
    # бы тупик — вопрос не виден, ответить нельзя, задание не закрыть, неделя
    # встала. Развязка согласована владельцем 31.08.
    hidden_until_done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Обязателен ли блок для перехода дальше по ленте (владелец 05.09.2026).
    # default=False, а не True, как у TrackerTask.is_required: этот блок мог
    # уже существовать и отрисовываться в основном обучении до стройки ленты —
    # True по умолчанию заставило бы старые блоки внезапно что-то
    # блокировать. Обязательность блок получает только когда её явно
    # проставит куратор в новой ленте.
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Предмет блока — «Рисунок»|«Композиция»|None (доступен обоим). Зеркало
    # TrackerTask.subject, значения — app.constants.MOCK_SUBJECTS. Нужен
    # именно на блоке, а не на всём задании: предобучение часть цикла ведёт
    # без деления на предметы, часть — с делением (владелец 03.09.2026).
    subject: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Период доступа — открывается по календарной дате/времени, а не по
    # действию ученика (владелец 03.09.2026, найдено при повторном разборе
    # созвона 06.09.2026): «теория и задания откроются только с 23 сентября
    # 0000» — независимо от того, выполнил ли ученик предыдущий блок. Это
    # ОТДЕЛЬНЫЙ гейт от is_required (тот про действие ученика, этот — про
    # календарь), они складываются: блок ждёт both. Хранится всегда в UTC
    # (полночь МСК выбранной даты, `app.services.tz.msk_midnight`, конвертация
    # на стороне API) — та же конвенция, что у `day_bounds`, чтобы наивное
    # время из SQLite в тестах трактовалось как UTC без сюрпризов.
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Явное разрешение куратора обойти последовательную блокировку — блок
    # доступен независимо от того, закрыты ли обязательные блоки перед ним
    # (владелец 06.09.2026). Раньше это было жёстко зашито на BLOCK_LINK
    # («ссылка на занятие видна сразу»), но созвон 03.09 показал конфликт:
    # для тарифа «Уверенный максимум» та же ссылка ДОЛЖНА оставаться
    # заблокированной до сдачи домашки. Явный флаг решает это без ветвления
    # по тарифу в коде — куратор просто не проставляет его для той версии
    # блока, где нужна обычная последовательная блокировка. Тарифный гейт и
    # opens_at этот флаг не обходит — только очередь «сначала предыдущее».
    bypass_sequence: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_task_blocks_order", "task_id", "sort_order"),
        Index("ix_task_blocks_video", "video_id"),
    )


class TaskBlockImage(Base):
    """Одна картинка блока-галереи. Порядок — `sort_order`.

    Отдельная таблица, а не пара колонок у блока: владелец 31.08.2026 попросил
    класть в один блок несколько снимков. Та же конструкция, что у
    `HomeworkImage`, вплоть до пары url+path — по `path` объект потом удаляют
    или переносят в S3.
    """

    __tablename__ = "task_block_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    block_id: Mapped[int] = mapped_column(
        ForeignKey("task_blocks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_s3_url: Mapped[str] = mapped_column(String(500), nullable=False)
    image_s3_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_task_block_images_order", "block_id", "sort_order"),
    )


class TaskBlockOption(Base):
    """Вариант ответа — только у блоков-вопросов типа single/multiple."""

    __tablename__ = "task_block_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    block_id: Mapped[int] = mapped_column(
        ForeignKey("task_blocks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Выбор этого варианта раскрывает у ученика поле свободного текста
    # (владелец 03.09.2026: «выбрал навык — сразу под ним пишет, почему»).
    # Текст живёт в TaskBlockAnswerOption.text — привязан к варианту, а не
    # к вопросу целиком, иначе несколько выбранных вариантов не могли бы
    # держать каждый свой независимый текст в одном TaskBlockAnswer.text.
    requires_text: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_task_block_options_order", "block_id", "sort_order"),
    )


class TaskBlockResponse(Base):
    """Заполнение блоков-вопросов одного элемента одним учеником.

    Уникальность по (`task_id`, `user_id`) — та же, что была у
    `task_quiz_responses`: один элемент дня — одно заполнение, повторная
    отправка обновляет ответы, а не заводит вторую строку.
    """

    __tablename__ = "task_block_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("task_id", "user_id", name="uq_task_block_response_task_user"),
        Index("ix_task_block_responses_task", "task_id"),
        # Под будущий экран «ответы одного ученика по всем заданиям»: без него
        # такое чтение шло бы перебором. Заводим сразу, чтобы не возвращаться
        # к схеме второй раз.
        Index("ix_task_block_responses_user", "user_id"),
    )


class TaskBlockAnswer(Base):
    """Ответ ученика на один блок-вопрос.

    Привязан к `block_id`, не к позиции: правка текста вопроса не рвёт уже
    сохранённые ответы (тот же приём, что был у `TaskQuizAnswer`).
    """

    __tablename__ = "task_block_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    response_id: Mapped[int] = mapped_column(
        ForeignKey("task_block_responses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    block_id: Mapped[int] = mapped_column(
        ForeignKey("task_blocks.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # «Просмотрено» в очереди проверки (владелец 31.08.2026): преподаватель или
    # куратор отметил, что разобрал ответ, и тот ушёл из очереди. Отметку
    # ставит и снимает только staff; ученик её видит, но снять не может.
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "response_id", "block_id", name="uq_task_block_answer_response_block"
        ),
        # Очередь читает «непросмотренные» — отбор идёт по этой колонке.
        Index("ix_task_block_answers_reviewed", "reviewed_at"),
    )


class TaskBlockAnswerOption(Base):
    """Выбранный вариант в ответе — одна строка на single, несколько на
    multiple. Копия `SurveyAnswerOption`."""

    __tablename__ = "task_block_answer_options"

    answer_id: Mapped[int] = mapped_column(
        ForeignKey("task_block_answers.id", ondelete="CASCADE"), primary_key=True
    )
    option_id: Mapped[int] = mapped_column(
        ForeignKey("task_block_options.id", ondelete="CASCADE"), primary_key=True
    )

    # Свободный текст под конкретным выбранным вариантом — заполняется только
    # когда у варианта TaskBlockOption.requires_text=True (владелец
    # 05.09.2026). Пусто у вариантов без этого флага.
    text: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaskBlockTariff(Base):
    """Тариф, которому доступен блок. Пусто — доступен всем тарифам.

    Зеркало `TrackerTaskTag`: тот же принцип «нормализованная таблица
    строк», а не JSON-поле (докстринг модуля выше). Канонические значения —
    `app.constants.TARIFFS`, проверка идёт в сервисном слое, не на уровне
    БД: переименование тарифа остаётся правкой одной строки в
    `constants.py`, а не миграцией (владелец 05.09.2026 — тарифы пока те
    же, что в constants.py, но должны легко переименовываться).
    """

    __tablename__ = "task_block_tariffs"

    block_id: Mapped[int] = mapped_column(
        ForeignKey("task_blocks.id", ondelete="CASCADE"), primary_key=True
    )
    tariff: Mapped[str] = mapped_column(String(50), primary_key=True)


class TaskBlockState(Base):
    """Состояние одного блока у конкретного ученика.

    `TaskBlockResponse` для этого не годится — она про заполнение вопросов
    всего задания разом (уникальность по task_id+user_id), а не про один
    блок: блоку без вопросов (видео, кнопка «Загрузить портфолио») вообще
    некуда было бы записать «выполнено». Строка заводится лениво, как у
    `TrackerTaskState` — нет строки, значит блок открыт.
    """

    __tablename__ = "task_block_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    block_id: Mapped[int] = mapped_column(
        ForeignKey("task_blocks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_OPEN)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Кто закрыл: None — закрыла система (то же соглашение, что у
    # TrackerTaskState.completed_by_id).
    completed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completion_source: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("block_id", "user_id", name="uq_task_block_state_block_user"),
        Index("ix_task_block_states_user", "user_id"),
    )
