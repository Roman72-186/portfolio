"""Тема недели для видеоуроков.

Собственная сущность видеомодуля, намеренно не связанная с ExamAssignment: у
пробников своя механика (предмет, билеты, таймер, период сдачи), и переплетение
уже приводило к тому, что урок мог выпасть ученику как вариант пробника.

Тема только открывается по дате — окна закрытия нет. Прошедшая тема остаётся в
каталоге как учебный архив: ученик может вернуться к материалу третьей недели,
сидя на седьмой.

**Два вида тем, различаются колонкой `kind`.** `week` — обычная тема недели,
которую заводит человек на странице видеоуроков. `program_item` — служебная
тема одного элемента учебной программы: её создаёт календарь, человек её не
видит и не выбирает. Служебные темы нужны затем, что аудиторию элемента
программы надо где-то хранить, а доступ к ролику в проекте умеет считать
только `accessible_topic_ids` по теме. Списки тем фильтруются по `kind`, иначе
выпадающий список на странице «Видео» зарастёт служебными строками. Любой новый
код, который пойдёт в `db.query(LearningTopic)` мимо `list_topics`, увидит и те
и другие — фильтр придётся ставить руками.

**Третий вид, `stage`** (владелец 24.09.2026, Этапы) — крупный период («месяц»,
«Предобучение»), группирующий несколько циклов (`kind='week'`) подряд.
Отдельной таблицы под этап не заводим — вторая сущность расписания в проекте
запрещена (`app/api/cabinet_program.py`), поэтому этап — такая же
`LearningTopic` с `kind='stage'`. Цикл ссылается на свой этап через
`parent_id`. Циклы, заведённые до 24.09.2026 (или созданные без выбора
этапа), остаются с `parent_id=NULL` — это легаси-архив без привязки к этапу,
не ошибка данных.

**Четвёртый вид, `period`** (владелец 06.10.2026, Периоды) — уровень над
этапом: «Предобучение», «1 семестр», «2 семестр». Структура программы —
Период → Этап → Цикл → задания. Этап ссылается на период тем же `parent_id`;
этап без периода (заведён до 06.10.2026 или период не выбран) — обычный этап,
как раньше. У периода `parent_id` всегда `NULL`, заданий на периоде нет
(владелец: «задания будут только внутри циклов»). Период добавлен сверху, а
не вставлен между этапом и циклом: на связке «цикл → этап» держится вся лента
ученика (карусель, долги, нумерация «Цикл N», задания этапа), её не трогали.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

TOPIC_KIND_WEEK = "week"
TOPIC_KIND_PROGRAM_ITEM = "program_item"
TOPIC_KIND_STAGE = "stage"
TOPIC_KIND_PERIOD = "period"


class LearningTopic(Base):
    __tablename__ = "learning_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # «week» — тема недели, её заводит человек. «program_item» — служебная тема
    # элемента учебной программы, её создаёт календарь (см. докстринг модуля).
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TOPIC_KIND_WEEK, server_default=TOPIC_KIND_WEEK
    )
    # Родитель по уровню программы (см. докстринг модуля): у цикла (`week`) —
    # этап `kind='stage'` (владелец 24.09.2026), у этапа — период
    # `kind='period'` (06.10.2026). У периода всегда NULL.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("learning_topics.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ссылка на созвон занятия недели (Zoom/Google Meet и т.п.), заполняется вручную.
    meeting_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Когда тема появляется у учеников. Верхней границы нет — см. докстринг модуля.
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Конец периода цикла (владелец 06.09.2026): «открываем, устанавливаем, с
    # какого по какое это будет цикл — то есть он три недели». До этого поля
    # период темы был всегда ровно календарной неделей: резолверы в
    # `services/tracker.py` брали понедельник от `opens_at` и добавляли шесть
    # дней. `None` — старое поведение (неделя от понедельника `opens_at`),
    # именно так читаются все темы, заведённые до 06.09.2026.
    #
    # Каталог видеоуроков этого поля не касается: тема там по-прежнему
    # открывается навсегда, `ends_at` ограничивает только цикл учебной
    # программы, а не доступ к материалу.
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Явный флаг, а не правило «нет тегов и нет назначенных значит всем»: молчаливая
    # раздача платного контента при забытой аудитории — слишком дорогая ошибка.
    assign_to_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Тарифная видимость (созвон 26.08.2026, TODO.md «Реализовать доступность
    # программы по тарифу») — отдельная от assign_to_all/тегов/поимённых
    # исключений ось: та адресация решает «кому», эта — «по какому тарифу»,
    # обе проверяются одновременно в accessible_topic_ids. default=False:
    # видимость по тарифу не сужена, ведёт себя как раньше. True + пустой
    # LearningTopicTariff — сознательно «скрыто от всех тарифов» (владелец
    # 30.08.2026): чек-бокс включили, но тариф ещё не отметили.
    tariff_restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Незакрытый цикл запирает следующие (владелец 03.10.2026: настройка рядом
    # с «Показывать ученикам»). Смысл — только у цикла (`kind='week'`): False —
    # долг этого цикла не держит ученика, дальше он идёт свободно. Правило
    # целиком — `tracker.cycle_debt` и `tracker.effective_cycle`. По умолчанию
    # True: так жили все циклы с 30.09.2026.
    locks_next: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
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
        Index("ix_learning_topics_public", "is_published", "opens_at"),
        Index("ix_learning_topics_kind", "kind", "opens_at"),
        Index("ix_learning_topics_parent", "parent_id"),
    )


class LearningTopicTag(Base):
    """Кому адресована тема по тегам. Сопоставление строгое, по tag_id."""

    __tablename__ = "learning_topic_tags"

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("learning_topics.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (
        Index("ix_learning_topic_tags_tag", "tag_id"),
    )


class LearningTopicTariff(Base):
    """Каким тарифам видна тема, когда `tariff_restricted=True`.

    Составной PK `(topic_id, tariff)`, не отдельный id-суррогат с
    уникальностью по topic_id — это намеренно оставляет место под будущую
    механику «уровень ученика внутри тарифа» (созвон 26.08.2026): к паре
    можно будет добавить nullable `min_level` одним `ALTER TABLE ADD COLUMN`,
    без разрушительной миграции. Саму механику уровней здесь не строим.

    **Окно доступа к циклу** (владелец 06.10.2026: «с какой даты по какое
    доступен ему данный цикл») — `opens_at`/`closes_at` строки. Цикл при этом
    идёт в свои общие даты (`LearningTopic.opens_at`/`ends_at`), окно решает
    только, когда его видит ученик этого тарифа: до `opens_at` цикла у него
    нет (`accessible_topic_ids`), после `closes_at` цикл у него в архиве —
    смотреть можно, делать нельзя, и долгом он больше не держит
    (`video_topics.tariff_closed_topic_ids`). NULL — своей границы нет. Окно
    заполняет только форма цикла; у служебных тем заданий и тем видео обе
    колонки пустые.
    """

    __tablename__ = "learning_topic_tariffs"

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("learning_topics.id", ondelete="CASCADE"), primary_key=True
    )
    tariff: Mapped[str] = mapped_column(String(50), primary_key=True)
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_learning_topic_tariffs_tariff", "tariff"),
    )


class LearningTopicAssignee(Base):
    """Поимённое исключение: тема выдана ученику сверх тегов.

    Для догоняющих и индивидуальных разборов — когда ученик не попадает ни в один
    тег адресации, но материал ему нужен.
    """

    __tablename__ = "learning_topic_assignees"

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("learning_topics.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_learning_topic_assignees_user", "user_id"),
    )
