"""Окно загрузки портфолио: сроки задаёт блок «Загрузить портфолио».

Владелец 18.09.2026: «Есть открытое окно — может загружать и удалять фото из
ДО, нет открытого окна — уже не может ни загрузить, ни удалить». Сроки берём
не из отдельной настройки, а из самого блока конструктора. Для новых блоков
куратор задаёт длительность в часах: отсчёт начинается отдельно у каждого
ученика, когда блок впервые становится ему доступен. Старые блоки без
длительности продолжают использовать `opens_at`/`closes_at`.

Экран `/upload` до этого модуля жил сам по себе — открытый всегда и никак не
связанный с заданием (гейт `FeaturePeriod` сняли 09.09.2026, оставив доступ
«на усмотрение задания», но само задание об этом никто не спрашивал). Здесь и
появляется недостающий вопрос: открыт ли ученику хоть один блок портфолио.

Своей копии правил доступа тут нет — считает `task_blocks.feed_state`, то есть
тот же `is_block_accessible`, что рисует ленту: `opens_at`, `closes_at`, тариф,
очередь предыдущих обязательных блоков и `bypass_sequence`. Иначе экран
загрузки и лента разошлись бы в оценке одного и того же блока.

**Блок без дат — окно открыто.** `opens_at`/`closes_at` необязательны, и
`is_block_accessible` такой блок пропускает. Это сознательная страховка прода:
у групп, где сроки не проставлены, загрузка работает как раньше.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

from app.models.task_block import BLOCK_PORTFOLIO, TaskBlock
from app.models.tracker import TrackerTask
from app.models.work import WORK_TYPE_AFTER, WORK_TYPE_BEFORE
from app.services.cycle_feed import cycle_is_archived_for_user
from app.services.task_blocks import feed_state, portfolio_window_deadline
from app.services.tracker import MONTH_GENITIVE, accessible_task_ids
from app.services.tz import MSK_TZ, now_msk, today_msk
from app.services.video_topics import accessible_topic_ids

# Раздел портфолио, в который грузит окно. Те же значения, что `Work.work_type`,
# и те же, что `section` в ссылке `/upload?section=…` — третьего словаря заводить
# не за чем.
SECTION_BEFORE = WORK_TYPE_BEFORE
SECTION_AFTER = WORK_TYPE_AFTER


@dataclass(frozen=True)
class PortfolioWindow:
    """Одно окно загрузки — один блок «Загрузить портфолио» у ученика."""

    block_id: int
    task_id: int
    section: str
    opens_at: datetime | None
    closes_at: datetime | None
    is_open: bool


def block_section(block: TaskBlock) -> str:
    """Раздел, в который грузит блок.

    Пока всегда «До» (владелец 09.09.2026: «по этой кнопке работы загружаются
    в ДО»). Шаг 2 заменит тело на колонку `portfolio_section` — вызывающий код
    от этого не меняется, поэтому раздел читается через функцию, а не константой
    по месту.
    """
    return SECTION_BEFORE


def _as_utc(value: datetime | None) -> datetime | None:
    """Наивное время из базы считаем UTC — так же, как `is_block_accessible`.

    SQLite в тестах отдаёт `datetime` без таймзоны, и без этой нормализации
    сравнение упало бы с TypeError на первом же блоке со сроком.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _portfolio_blocks_by_task(db: DBSession, user_id: int) -> dict[int, list[TaskBlock]]:
    """Блоки «Загрузить портфолио» из заданий, доступных ученику.

    Адресацию берём готовую: `accessible_topic_ids` (задания внутри программы)
    и `accessible_task_ids` (разовые, `topic_id IS NULL`) — те же две выборки,
    что фильтруют доступ в `cabinet_tracker._accessible_task_or_404`.
    """
    topic_ids = accessible_topic_ids(db, user_id)
    task_ids = accessible_task_ids(db, user_id)
    if not topic_ids and not task_ids:
        return {}

    rows = (
        db.query(TaskBlock, TrackerTask)
        .join(TrackerTask, TrackerTask.id == TaskBlock.task_id)
        .filter(
            TaskBlock.block_type == BLOCK_PORTFOLIO,
            TrackerTask.deleted_at.is_(None),
            TrackerTask.is_published.is_(True),
        )
        .all()
    )

    by_task: dict[int, list[TaskBlock]] = {}
    for block, task in rows:
        accessible = (task.topic_id is not None and task.topic_id in topic_ids) or (
            task.topic_id is None and task.id in task_ids
        )
        if accessible:
            by_task.setdefault(task.id, []).append(block)
    return by_task


def intake_portfolio_gate_required(db: DBSession, *, user_id: int) -> bool:
    """Нужно ли новичку с «Пробы» закрывать кабинет портфолио-гейтом.

    Глобальный гейт должен следовать той же настройке, что и блок в учебной
    ленте. Учитываем только опубликованные задания, доступные конкретному
    ученику; необязательное задание не может закрывать остальные разделы.
    """
    for task_id, blocks in _portfolio_blocks_by_task(db, user_id).items():
        task = db.get(TrackerTask, task_id)
        if task and task.is_required and any(
            block.is_required_for_intake for block in blocks
        ):
            return True
    return False


def portfolio_windows(
    db: DBSession,
    *,
    user_id: int,
    user_tariff: str | None,
    section: str | None = None,
) -> list[PortfolioWindow]:
    """Все окна портфолио ученика — и открытые, и закрытые.

    `feed_state` зовём один раз на задание, а не на блок: в задании обычно один
    блок портфолио, но правило доступа всё равно считается по всей цепочке
    блоков сразу.
    """
    windows: list[PortfolioWindow] = []
    for task_id, blocks in _portfolio_blocks_by_task(db, user_id).items():
        wanted_ids = {block.id for block in blocks}
        # Этапы (владелец 24.09.2026): цикл задания стал архивным для ученика —
        # окно портфолио запирается тем же условием, что трекер и домашка, не
        # трогая саму `feed_state`/`is_block_accessible` (инвариант «не трогать
        # precourse-логику» — архивность проверяется здесь дополнительно).
        task = db.get(TrackerTask, task_id)
        archived = (
            task is not None
            and task.topic_id is not None
            and cycle_is_archived_for_user(db, user_id, task.topic_id, today_msk())
        )
        for entry in feed_state(
            db, task_id=task_id, user_id=user_id, user_tariff=user_tariff
        ):
            block = entry["block"]
            if block.id not in wanted_ids:
                continue
            block_sec = block_section(block)
            if section is not None and block_sec != section:
                continue
            state = entry["state"]
            personal_deadline = portfolio_window_deadline(block, state)
            uses_personal_window = bool(block.portfolio_window_hours)
            windows.append(
                PortfolioWindow(
                    block_id=block.id,
                    task_id=task_id,
                    section=block_sec,
                    opens_at=(
                        _as_utc(state.started_at)
                        if uses_personal_window and state is not None
                        else _as_utc(block.opens_at)
                    ),
                    closes_at=(
                        _as_utc(personal_deadline)
                        if uses_personal_window else _as_utc(block.closes_at)
                    ),
                    is_open=(
                        not archived
                        and entry["status"] != "locked"
                        and (not uses_personal_window or personal_deadline is not None)
                    ),
                )
            )
    return windows


@dataclass(frozen=True)
class WindowSnapshot:
    """Окна раздела одним расчётом: открытое, последнее закрывшееся, есть ли они вообще.

    Три отдельных вопроса к одному и тому же списку — а список стоит обхода
    всех доступных заданий с расчётом доступа по каждому, поэтому считаем один
    раз и раскладываем на месте.
    """

    open: PortfolioWindow | None
    last_closed: PortfolioWindow | None
    any_exists: bool


def snapshot(
    db: DBSession,
    *,
    user_id: int,
    user_tariff: str | None,
    section: str | None = None,
    preferred_block_id: int | None = None,
) -> WindowSnapshot:
    windows = portfolio_windows(
        db,
        user_id=user_id,
        user_tariff=user_tariff,
        section=section,
    )
    now = now_msk()
    open_windows = [w for w in windows if w.is_open]
    closed = [
        w for w in windows
        if not w.is_open and w.closes_at is not None and w.closes_at <= now
    ]
    return WindowSnapshot(
        open=_pick_open(open_windows, preferred_block_id),
        last_closed=max(closed, key=lambda w: w.closes_at) if closed else None,
        any_exists=bool(windows),
    )


def _pick_open(
    open_windows: list[PortfolioWindow], preferred_block_id: int | None
) -> PortfolioWindow | None:
    if not open_windows:
        return None
    if preferred_block_id is not None:
        for window in open_windows:
            if window.block_id == preferred_block_id:
                return window
    # Без подсказки берём то, что закроется раньше: именно его срок ученику и
    # нужно успеть, и именно его дату честно показывать на экране.
    return min(
        open_windows,
        key=lambda w: (
            w.closes_at is None,
            w.closes_at or datetime.max.replace(tzinfo=timezone.utc),
        ),
    )


def find_open_portfolio_window(
    db: DBSession,
    *,
    user_id: int,
    user_tariff: str | None,
    section: str | None = None,
    preferred_block_id: int | None = None,
) -> PortfolioWindow | None:
    """Открытое сейчас окно загрузки или `None`.

    `preferred_block_id` — блок, с кнопки которого ученик пришёл: если открыты
    сразу два окна, берём то, про которое он и спрашивал. Прав этот параметр не
    даёт, окно всё равно проверяется целиком, поэтому подставленный руками
    чужой id ничего не открывает.
    """
    return snapshot(
        db, user_id=user_id, user_tariff=user_tariff, section=section,
        preferred_block_id=preferred_block_id,
    ).open


def has_portfolio_window(
    db: DBSession,
    *,
    user_id: int,
    user_tariff: str | None,
    section: str | None = None,
) -> bool:
    """Заведено ли ученику хоть одно окно — открытое, будущее или прошедшее.

    Отделяет «срок прошёл» от «сроками тут никто не управляет»: у групп без
    блока портфолио загрузка должна работать как раньше, иначе правило потока
    предобучения молча отрезало бы им портфолио целиком.
    """
    return snapshot(
        db, user_id=user_id, user_tariff=user_tariff, section=section
    ).any_exists


def find_last_closed_window(
    db: DBSession,
    *,
    user_id: int,
    user_tariff: str | None,
    section: str | None = None,
) -> PortfolioWindow | None:
    """Последнее закрывшееся окно — только ради текста «работы принимали до …».

    Блоки, которые ещё не открылись или заперты очередью, сюда не попадают: у
    них `closes_at` либо впереди, либо его нет, и фразой про прошедший срок
    объяснять их нечем.
    """
    return snapshot(
        db, user_id=user_id, user_tariff=user_tariff, section=section
    ).last_closed


def format_deadline_msk(value: datetime | None) -> str:
    """«19 сентября, 23:59» — срок для ученика, в московском времени.

    В базе момент лежит в UTC (`parse_msk_local` при записи), поэтому без
    перевода в МСК «до 19 сентября, 23:59» превратилось бы в «20:59».
    """
    if value is None:
        return ""
    local = _as_utc(value).astimezone(MSK_TZ)
    return f"{local.day} {MONTH_GENITIVE[local.month]}, {local:%H:%M}"
