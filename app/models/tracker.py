"""«Личный трекер» ученика: задачи от преподавателя и дайджест-расписание месяца.

Задачи заводит Главный преподаватель (ранг роли >= 4), ученик свои задачи не
добавляет — решение владельца от 20.08. Адресация скопирована с LearningTopic
(явный флаг «всем» + теги + поимённые исключения) и сопоставляется строго по
tag_id: эвристика «Р»/«К» как маркеров предмета в проде означает группу и
уровень куратора и уже прятала билеты от учеников (см. TODO.md §7).

Состояние задачи у конкретного ученика вынесено в отдельную таблицу
TrackerTaskState: одна задача адресована многим, статус у каждого свой.

Завершение задачи — смешанное (владелец, 20.08): где у задачи есть
событие-источник (загрузил работу, досмотрел видео), гасит система; где
источника нет, ученик отмечает галочкой сам. Остальные режимы заложены
колонкой completion_mode и включаются без миграции.

Полный разбор решений — plans/2026-08-20-apparchi-tracker-and-digest.md.
"""
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


# Как задача переходит в «выполнено». В коде включён COMPLETION_MIXED, остальные
# значения держим готовыми: смена режима — это UPDATE строки, а не миграция.
COMPLETION_MANUAL = "manual"      # только галочка ученика
COMPLETION_AUTO = "auto"          # только событие-источник
COMPLETION_STAFF = "staff"        # закрывает куратор при проверке
COMPLETION_MIXED = "auto_or_manual"  # событие есть — система; нет — ученик

COMPLETION_MODES = (
    COMPLETION_MANUAL, COMPLETION_AUTO, COMPLETION_STAFF, COMPLETION_MIXED,
)

# Откуда система узнаёт, что задача выполнена. None — события нет, гасится руками.
SOURCE_EXAM_ASSIGNMENT = "exam_assignment"  # сдача пробника
SOURCE_LEARNING_TOPIC = "learning_topic"    # просмотр видео недели
SOURCE_FEEDBACK = "feedback"                # ответ в диалоге обратной связи
SOURCE_HOMEWORK = "homework"                # сдача домашней работы

SOURCE_KINDS = (
    SOURCE_EXAM_ASSIGNMENT, SOURCE_LEARNING_TOPIC, SOURCE_FEEDBACK, SOURCE_HOMEWORK,
)

# Тип элемента программы — что преподаватель ставит в день недели. Отличается от
# source_kind: тип отвечает на «что это», источник — на «по какому событию гасить».
ITEM_VIDEO = "video"          # посмотреть видео недели
ITEM_HOMEWORK = "homework"    # домашнее задание
ITEM_MOCK_EXAM = "mock_exam"  # пробник
ITEM_SURVEY = "survey"        # анкета
ITEM_LESSON = "lesson"        # занятие или эфир
ITEM_MATERIAL = "material"    # материалы недели
ITEM_QUIZ = "quiz"            # тест по теории
ITEM_CHECKLIST = "checklist"  # чек-лист и проверки
ITEM_OTHER = "other"          # всё остальное, в том числе разовые задачи вне недели

ITEM_KINDS = (
    ITEM_VIDEO, ITEM_HOMEWORK, ITEM_MOCK_EXAM, ITEM_SURVEY, ITEM_LESSON,
    ITEM_MATERIAL, ITEM_QUIZ, ITEM_CHECKLIST, ITEM_OTHER,
)

# Подписи рядом с типами: их показывают и конструктор, и календарь программы, и
# позже экран ученика.
ITEM_KIND_LABELS = {
    ITEM_VIDEO: "Видеоматериал",
    ITEM_HOMEWORK: "Самостоятельная работа",
    ITEM_MOCK_EXAM: "Пробник",
    ITEM_SURVEY: "Анкета",
    ITEM_LESSON: "Занятие",
    ITEM_MATERIAL: "Материал",
    ITEM_QUIZ: "Тест по теории",
    ITEM_CHECKLIST: "Чек-лист и проверки",
    ITEM_OTHER: "Другое",
}

# Порядок восьми вкладок недели на «Актуальном образовательном пространстве» —
# решение владельца 22.08 (уточнено текстом и повторно в чате 23.08), см.
# plans/2026-08-22-apparchi-student-cabinet-open-questions.md, п.7. Порядок
# фиксирован, пока не появится конструктор последовательностей блоков (п.14,
# отдельная будущая стройка) — тогда станет настраиваемым.
#
# TAB_KIND_FEEDBACK — виртуальная восьмая вкладка: у неё нет TrackerTask.kind,
# это задел под шаг 4 (возврат «Обратной связи» поверх ExamCycle/Feedback).
TAB_KIND_FEEDBACK = "feedback"

WEEK_TAB_SEQUENCE = (
    ITEM_MATERIAL, ITEM_VIDEO, ITEM_QUIZ, ITEM_LESSON,
    ITEM_HOMEWORK, ITEM_CHECKLIST, ITEM_SURVEY, TAB_KIND_FEEDBACK,
)

WEEK_TAB_LABELS = {
    ITEM_MATERIAL: "Материалы",
    ITEM_VIDEO: "Видео",
    ITEM_QUIZ: "Тест по теории",
    ITEM_LESSON: "Занятие",
    ITEM_HOMEWORK: "Задание",
    ITEM_CHECKLIST: "Чек-лист и проверки",
    ITEM_SURVEY: "Анкета",
    TAB_KIND_FEEDBACK: "Обратная связь",
}

STATUS_OPEN = "open"
STATUS_DONE = "done"


class TrackerTask(Base):
    """Задача, которую преподаватель ставит ученикам."""

    __tablename__ = "tracker_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Дедлайн: от него считается цвет строки у ученика. Считать только через
    # app/services/tz.py — в контейнере UTC, date.today() сдвигает границу на
    # три часа и задача краснеет раньше срока.
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Когда задача появляется у ученика. None — сразу после публикации.
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subject: Mapped[str | None] = mapped_column(String(50), nullable=True)  # «Рисунок» | «Композиция» | None

    # Неделя программы, в которую поставлен элемент. None — разовая задача вне
    # программы. Неделя — это LearningTopic видеомодуля: расписание, публикация
    # и адресация там уже обкатаны, второй такой сущности не заводим
    # (plans/2026-08-20…, Этап 2 «Конструктор недели»).
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("learning_topics.id", ondelete="SET NULL"), nullable=True
    )
    # Что это за элемент: видео, домашка, пробник, анкета, занятие.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=ITEM_OTHER)
    # Порядок внутри одного дня: дата задаёт день, sort_order — что раньше внутри дня.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    completion_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default=COMPLETION_MIXED
    )
    # Событие, по которому система гасит задачу сама. Заполняется парой:
    # без source_kind значение source_id смысла не имеет.
    source_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Явный флаг, а не «нет тегов значит всем»: молчаливая раздача задачи всей
    # школе — та же дорогая ошибка, что у тем видеоуроков.
    assign_to_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
        Index("ix_tracker_tasks_public", "is_published", "due_at"),
        Index("ix_tracker_tasks_source", "source_kind", "source_id"),
        Index("ix_tracker_tasks_topic", "topic_id", "due_at", "sort_order"),
    )


class TrackerTaskTag(Base):
    """Кому адресована задача по тегам. Сопоставление строгое, по tag_id."""

    __tablename__ = "tracker_task_tags"

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (
        Index("ix_tracker_task_tags_tag", "tag_id"),
    )


class TrackerTaskAssignee(Base):
    """Поимённое исключение: задача выдана ученику сверх тегов."""

    __tablename__ = "tracker_task_assignees"

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_tracker_task_assignees_user", "user_id"),
    )


class TrackerTaskState(Base):
    """Состояние задачи у конкретного ученика.

    Строка заводится лениво — в момент первой отметки или автозакрытия. Пока
    строки нет, задача считается открытой: заранее плодить строку на каждую
    пару «задача × ученик» незачем.
    """

    __tablename__ = "tracker_task_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_OPEN)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Кто закрыл: сам ученик, куратор или staff. None — закрыла система.
    completed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Чем закрылась фактически — при смешанном режиме это единственный способ
    # отличить реальную сдачу от самоотметки.
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
        UniqueConstraint("task_id", "user_id", name="uq_tracker_task_state_task_user"),
        Index("ix_tracker_task_states_user_status", "user_id", "status"),
    )


class ScheduleDigest(Base):
    """Расписание на месяц: статичный блок, утверждается в конце месяца и
    публикуется в начале — весь месяц не меняется.

    Дайджестов на один месяц может быть несколько: адресация разная по группам
    и тарифам (решение владельца от 20.08), поэтому уникальности по (year,
    month) намеренно нет.
    """

    __tablename__ = "schedule_digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)  # 1–12

    assign_to_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
        Index("ix_schedule_digests_period", "year", "month", "is_published"),
    )


class ScheduleDigestTag(Base):
    """Кому адресован дайджест по тегам. Строго по tag_id, как у задач."""

    __tablename__ = "schedule_digest_tags"

    digest_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_digests.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (
        Index("ix_schedule_digest_tags_tag", "tag_id"),
    )


class ScheduleDigestAssignee(Base):
    """Поимённое исключение: дайджест выдан ученику сверх тегов."""

    __tablename__ = "schedule_digest_assignees"

    digest_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_digests.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_schedule_digest_assignees_user", "user_id"),
    )


# Типы событий в расписании — из макета созвона 17.08.
EVENT_DEADLINE = "deadline"      # дедлайн сдачи
EVENT_LESSON = "lesson"          # занятие
EVENT_MOCK_EXAM = "mock_exam"    # окно пробника («с 25 по 30»)
EVENT_BROADCAST = "broadcast"    # общий эфир

EVENT_KINDS = (EVENT_DEADLINE, EVENT_LESSON, EVENT_MOCK_EXAM, EVENT_BROADCAST)


class ScheduleEvent(Base):
    """Строка внутри дайджеста. Диапазон дат нужен для окон вроде пробника —
    у однодневного события ends_on равен starts_on."""

    __tablename__ = "schedule_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    digest_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_digests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)

    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)

    # Ссылка на созвон — по требованию созвона 17.08 зашивается в кнопку, а не
    # показывается текстом, который надо копировать.
    meeting_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_schedule_events_digest_date", "digest_id", "starts_on"),
    )
