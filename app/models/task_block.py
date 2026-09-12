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

**`kind` при этом не отвечает на вопрос «что можно положить».** С 06.09.2026
он не отвечает и на вопрос «куда попадёт карточка»: восьми вкладок недели
больше нет, лента идёт по `due_at` и `sort_order`
(`services/cycle_feed.py`). У `kind` осталась служебная роль — за ним стоит
механика (билеты пробника, привязка ролика, приём работ) и отчётность.

Блоки — это содержимое, но с 05.09.2026 они **гасятся по отдельности**
(`TaskBlockState`): в ленте обязательный незакрытый блок запирает всё, что
ниже, включая блоки следующих заданий. Сама задача закрывается как и раньше:
галочкой ученика или событием-источником (`source_kind`).

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
# Кнопка «Загрузить портфолио» (владелец 03.09.2026: «добавить кнопку загрузить
# портфолио — эта кнопка перенесёт сразу на готовый наш функционал… только
# здесь нужно сделать так, что он обязан загрузить это портфолио»). Своего
# хранилища у блока нет: он ведёт на существующий экран загрузки работ и
# закрывается сам, когда ученик загрузил работу внутри периода цикла.
BLOCK_PORTFOLIO = "portfolio"
# Диагностика навыков (владелец 03.09.2026): «есть навыки, уровень
# сформированности навыка… оцени, насколько ты стрессоустойчивый… и ребёнок
# отмечает 3 из 10 или 5 из 10». Варианты блока — названия навыков, ответ —
# оценка по каждому. Оценки копятся: «в начале обучения было так, в середине
# вот так» — сравнение по датам живёт в личной информации ученика.
BLOCK_SCALE = "scale"
# Контрольная работа на время (владелец 03.09.2026): «здесь в контрольной у нас
# таймер… давай сделаем один час… мы будем отслеживать статистику, сколько детей
# превысили время… их можно будет пометить красненьким». Ученик жмёт «Начать»,
# рисует и загружает работу; система считает, уложился он в лимит или нет.
BLOCK_TIMED = "timed"
# Приём работ прямо в задании (владелец 07.09.2026: «работы нужно загружать в
# заданиях»). В отличие от BLOCK_PORTFOLIO ученик никуда не уходит: файлы
# грузятся здесь же и привязываются к блоку (`TaskBlockSubmission`), а куратор
# видит их на едином экране проверки по ученику.
BLOCK_UPLOAD = "upload"
# Список правил с галочкой у каждого (владелец 03.09.2026: «он должен их
# прочитать и поставить галочки рядом с этими правилами, что он с ними
# ознакомился», подтверждено 07.09.2026). Варианты блока — сами правила,
# `body` — текст согласия над списком.
#
# Почему не вопрос с несколькими вариантами, которым это собирали до
# 07.09.2026: у вопроса «ответил» значит «отметил хоть что-то», а здесь шаг
# закрывается, только когда отмечены **все** правила. И вердикта «верно /
# неверно» тут нет — согласие не проверяют на правильность
# (`grade_response` берёт только BLOCK_QUESTION, так что фильтровать нечего).
BLOCK_RULES = "rules"
# Комбинированный блок «Фото + сдача работы» (владелец 12.09.2026: «нужно
# сделать ещё один блок универсальный, где будем загружать фото и по ним
# ученики будут делать работы и здесь же загружать работы»). Своей механики
# нет — переиспользует обе уже готовые: галерея-задание — те же
# `TaskBlockImage`, что у BLOCK_PHOTO, приём результата — тот же
# `TaskBlockSubmission`, что у BLOCK_UPLOAD. Отдельный тип, а не расширение
# одного из двух: куратору нужно выбирать плиткой между «просто фото»,
# «просто сдача» и «фото + сдача», а не переключателем внутри существующих.
BLOCK_PHOTO_UPLOAD = "photo_upload"

# Час на контрольную — число из созвона 03.09.2026 («давай сделаем один час»).
TIMED_DEFAULT_MINUTES = 60

# Сколько файлов ученик кладёт в одну сдачу. Десять — как у блока-галереи
# преподавателя (MAX_BLOCK_IMAGES) и у промежуточных фото пробника: общий
# потолок в проекте, отдельного числа этот блок не заслуживает.
MAX_SUBMISSION_IMAGES = 10

# Верхняя граница шкалы. Десять — из формулировки владельца («3 из 10»).
SCALE_MAX = 10
# Нижняя граница (владелец 12.09.2026, анкета «Метакомпетенции»: «шкала от 0
# до 10» — 0 сам по себе содержательный ответ, «навык вообще не развит», а не
# «ещё не отвечено»). До 12.09 ползунок начинался с 1 — это правка диапазона,
# не смена конвенции.
SCALE_MIN = 0

# Новый тип — строка здесь плюс ветка в шаблоне-рендере, миграция не нужна:
# специализированные колонки уже nullable, общего JSON-поля намеренно нет
# (в проекте нет ни одного JSONB, все списки — нормализованные таблицы).
BLOCK_TYPES = (
    BLOCK_TEXT, BLOCK_PHOTO, BLOCK_VIDEO, BLOCK_LINK, BLOCK_QUESTION,
    BLOCK_PORTFOLIO, BLOCK_SCALE, BLOCK_TIMED, BLOCK_UPLOAD, BLOCK_RULES,
    BLOCK_PHOTO_UPLOAD,
)

# Блоки, которые ученик закрывает загрузкой работы. Список нужен и роуту
# приёма файлов, и ленте: у «работы на время» к загрузке добавляется таймер,
# в остальном механика одна. BLOCK_PHOTO_UPLOAD закрывается тем же приёмом,
# что и BLOCK_UPLOAD — фото-задание к закрытию отношения не имеет.
SUBMISSION_BLOCK_TYPES = (BLOCK_UPLOAD, BLOCK_TIMED, BLOCK_PHOTO_UPLOAD)

BLOCK_TYPE_LABELS = {
    BLOCK_TEXT: "Текст",
    BLOCK_PHOTO: "Фото",
    BLOCK_VIDEO: "Видео",
    BLOCK_LINK: "Ссылка",
    BLOCK_QUESTION: "Вопрос",
    BLOCK_PORTFOLIO: "Загрузить портфолио",
    BLOCK_SCALE: "Шкала навыков",
    BLOCK_TIMED: "Работа на время",
    BLOCK_UPLOAD: "Загрузить работы",
    BLOCK_RULES: "Правила с галочками",
    BLOCK_PHOTO_UPLOAD: "Фото + сдача работы",
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

    # Закрытие доступа по календарю — обратная сторона `opens_at` (владелец
    # 10.09.2026, механика предобучения 17.09-03.10: «27 сентября в 23:30
    # закрывается доступ к образовательному модулю на платформе»). Условие
    # закрытия несёт не только дату, но и время суток — поэтому здесь нет
    # своего `msk_midnight`-помощника, как у `opens_at`: конвертацию в UTC
    # делает `app.services.tz.parse_msk_local`, которая принимает строку
    # `datetime-local` целиком. Ни с чем не складывается неявно — блок,
    # закрытый по времени, просто становится недоступен, как если бы у него
    # истёк срок; is_required-блок с прошедшим closes_at перестаёт
    # блокировать хвост ленты той же логикой, что уже применяется к
    # тарифно-недоступному обязательному блоку (`is_block_accessible`) —
    # требовать выполнения того, что закрылось навсегда, было бы тупиком без
    # выхода.
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Текст, который видит ученик вместо стандартной фразы «Откроется …» /
    # «Доступ закрыт», пока блок заперт по календарю — ни `opens_at`, ни
    # `closes_at` сами по себе объяснить причину так, как это нужно куратору,
    # не могут (владелец 10.09.2026: «Задание откроется 23 сентября, пока что
    # проверь чат-комьюнити в телеграмме и ответь на вопросы там»). Пусто —
    # рендер ленты показывает свою стандартную фразу.
    locked_message: Mapped[str | None] = mapped_column(String(300), nullable=True)

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

    # Лимит на работу в минутах — только у блока «Работа на время». Ученик
    # начинает сам, отсчёт идёт от нажатия; превышение не мешает сдать, но
    # попадает в статистику (владелец 03.09.2026: «сколько детей превысили
    # время… пометить красненьким»).
    time_limit_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

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

    # Три поля ниже — только у BLOCK_SCALE (владелец 11.09.2026: анкета
    # «Метакомпетенции», у каждого навыка своё описание и пояснение к краям
    # шкалы 0 и 10). NULL у вопросов и правил — та же конвенция, что у
    # video_id/url/question_type в TaskBlock: специализированные колонки
    # nullable у чужих типов, полиморфных таблиц вложений в проекте нет.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scale_min_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scale_max_label: Mapped[str | None] = mapped_column(String(200), nullable=True)

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


class TaskBlockRequiredTariff(Base):
    """Тариф, которому обязательно выполнение блока. Пусто — обязательно всем,
    кому блок виден (владелец 10.09.2026: на дешёвом тарифе ученик всё делает
    самостоятельно без сдачи, на топовом — сдача обязательна, при этом блок
    виден обоим).

    Отдельная ось от `TaskBlockTariff`: та решает «кому видно», эта — «с кого
    требовать» (`is_block_accessible` проверяет обе разом). Зеркало
    `TaskBlockTariff` один в один, включая канонические значения из
    `app.constants.TARIFFS`.
    """

    __tablename__ = "task_block_required_tariffs"

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
    # Когда ученик нажал «Начать» у блока «Работа на время». У остальных типов
    # пусто: строка состояния там заводится в момент выполнения, а не старта.
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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


class TaskBlockSubmission(Base):
    """Работа, которую ученик сдал прямо в блоке задания.

    Заведена 07.09.2026 по требованию владельца «работы нужно загружать в
    заданиях». До неё блоки «Загрузить портфолио» и «Работа на время» только
    уводили ученика ссылкой на общий экран `/upload`: файл попадал в портфолио
    (`Work`), к заданию не привязывался, а блок закрывался фактом любой новой
    работы за период цикла — включая загруженную совсем по другому поводу.

    Ключ — (`block_id`, `user_id`), а не (`task_id`, `user_id`), как у
    `HomeworkSubmission`: в одном задании может стоять несколько блоков приёма
    работ (контрольная на время плюс обычная сдача), и задачный ключ склеил бы
    их в одну сдачу. Та же причина, по которой отдельно от `TaskBlockResponse`
    живёт `TaskBlockState`.

    Своей моделью, а не колонкой в `Work`: `app/models/homework.py` описывает,
    как переплетение домашки с `Work`/`ExamCycle` один раз уже уронило доступ
    к урокам. Портфолио остаётся портфолио, сдача по заданию — отдельной
    сущностью с собственным статусом проверки.
    """

    __tablename__ = "task_block_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    block_id: Mapped[int] = mapped_column(
        ForeignKey("task_blocks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Общее описание к работе (владелец, вопрос 7 в ВОПРОСЫ-ПО-ЛЕНТЕ.md:
    # «до 10 фотографий с общим описанием»). Одно на сдачу, не на снимок.
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Момент сдачи. Отдельно от created_at: строка заводится при первой
    # загрузке файла, а пересдача до проверки обновляет именно этот момент —
    # по нему куратор видит, что работа приехала заново.
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Проверка куратором. Пусто — работа ждёт в очереди «непроверенное»
    # (`services/review_aggregate.py`), тот же предикат, что у `Work.score`.
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

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
        UniqueConstraint("block_id", "user_id", name="uq_task_block_submission_block_user"),
        Index("ix_task_block_submissions_user", "user_id"),
    )


class TaskBlockSubmissionImage(Base):
    """Один файл сданной работы. Пара url+path — как у `TaskBlockImage`."""

    __tablename__ = "task_block_submission_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("task_block_submissions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    image_s3_url: Mapped[str] = mapped_column(String(500), nullable=False)
    image_s3_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_task_block_submission_images_order", "submission_id", "sort_order"),
    )
