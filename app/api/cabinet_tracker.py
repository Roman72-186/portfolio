"""«Личный трекер» ученика — плоский список: что горит, что в работе, что закрыто.

По макету созвона 17.08 это не календарь и не разбивка по дням — вертикальный
список задач с цветным статусом (`ITEM_KIND_LABELS`/`task_status`), как
Trello-чеклист без досок. Разбивка по дням недели и календарная полоска — это
`/cabinet/learning` («Актуальное образовательное пространство»), сюда она не
относится (см. `session-handoffs/current-program.md`, правка от 21.08).

Просроченное копится без нижней границы по времени — долг не имеет смысла
терять после смены недели, ученик должен видеть его, пока не закроет. Рамка
— период, в котором ученик сейчас (`cycle_feed.current_period`, владелец
06.10.2026): прошлые периоды и текущий видны, будущие — нет.
Выборка задач и их статус — общий движок `accessible_task_entries` из
`app/services/tracker.py`, тот же самый, что использует `/cabinet/learning`.

Раньше `/cabinet/tracker` был вторым именем общего дашборда ученика
(`cabinet_student.py`) — сюда переехала только его роль в навигации
(`STUDENT_NAV_ITEMS[key="tracker"]`), сам маршрут теперь самостоятельный.

Hero-карточка (аватар/имя/тариф/баллы Р-К/год поступления) — решение владельца
21.08: живёт здесь, не в АОП. Partial `partials/profile_hero.html`, стили —
`app/static/css/profile_hero.css`, данные по баллам — общий сервис
`app/services/stats.py::avg_score_by_subject_all_time` (тот же, что у карточки
ученика для персонала).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.db.database import get_db
from app.dependencies import require_csrf_header, require_student
from app.models.learning_topic import TOPIC_KIND_STAGE, TOPIC_KIND_WEEK, LearningTopic
from app.models.learning_video import LearningVideo
from app.models.task_block import (
    BLOCK_COMPARE, BLOCK_LINK, BLOCK_MEDIA, BLOCK_PHOTO, BLOCK_PHOTO_UPLOAD, BLOCK_PORTFOLIO, BLOCK_QUESTION, BLOCK_RULES,
    BLOCK_SCALE, BLOCK_TIMED, BLOCK_UPLOAD, BLOCK_VIDEO, MAX_BLOCKS,
    QUESTION_TEXT,
    SCALE_MAX, SCALE_MIN, SUBMISSION_BLOCK_TYPES, TaskBlock, TaskBlockAnswer,
    TaskBlockSubmissionImage,
)
from app.models.tracker import (
    ITEM_HOMEWORK,
    ITEM_MOCK_EXAM,
    STATUS_DONE,
    STATUS_OPEN,
    TrackerTask,
    TrackerTaskState,
)
from app.models.user import User
from app.models.work import WORK_TYPE_BEFORE, Work
from app.services.program import (
    WEEKDAY_LABELS,
    day_bounds,
    msk_date,
    week_start,
)
from app.services.cycle_feed import (
    LINK_LOCKED_DETAIL,
    block_step_is_open,
    current_feed_task_ids,
    current_period,
    deadline_view,
    entries_up_to_period,
    entries_open_to_student,
    task_is_archived_for_user,
    task_is_locked_for_user,
)
from app.services.portfolio_window import (
    format_deadline_msk,
    portfolio_windows,
)
from app.services.point_a import point_a_level, student_point_a
from app.services.stats import avg_score_by_subject_all_time
from app.services.submission_edit import (
    block_work_reason, deadline_reason, late_first_submission,
)
from app.services import s3 as s3_service
from app.services.task_blocks import (
    add_submission_image as add_task_block_submission_image,
    answered_block_ids as task_block_answered_ids,
    close_block_for_user as close_task_block_for_user,
    completion_blocker as task_completion_blocker,
    CompareChoiceError,
    compare_pick_url,
    compare_progress,
    completed_after_deadline as task_block_completed_after_deadline,
    save_compare_step,
    count_submission_images as count_task_block_submission_images,
    delete_submission_images as delete_task_block_submission_images,
    is_submission_complete,
    submission_photo_limit,
    get_submit_deadlines as get_task_block_submit_deadlines,
    get_task_submit_deadlines as get_task_level_submit_deadlines,
    submit_deadline_for,
    get_answers_map as get_task_block_answers_map,
    get_or_create_submission as get_or_create_task_block_submission,
    get_submission as get_task_block_submission,
    list_submission_images as list_task_block_submission_images,
    mark_submitted as mark_task_block_submitted,
    get_blocks as get_task_blocks,
    get_blocks_for_tasks as get_task_blocks_for_tasks,
    task_content_label,
    get_option_images as get_task_block_option_images,
    get_images as get_task_block_images,
    grade_response as grade_task_blocks,
    get_options as get_task_block_options,
    get_response as get_task_block_response,
    get_selected_option_texts as get_task_block_selected_option_texts,
    get_selected_options as get_task_block_selected_options,
    get_state as get_task_block_state,
    poll_submission_error,
    question_blocks as task_question_blocks,
    BlockViewer,
    visible_blocks_for_student,
    start_timed_block as start_task_timed_block,
    timed_overrun as task_block_timed_overrun,
    timed_seconds_left as task_block_timed_seconds_left,
    save_response as save_task_block_response,
)
from app.services.tracker import (
    STUDENT_ROLE_RANK,
    accessible_task_entries,
    accessible_task_ids,
    active_digest_for_student,
    active_goal_for_student,
    cycle_debt,
    cycle_label,
    digest_calendar,
    digest_day_events,
    digest_events_with_edges,
    digest_heading,
    effective_week_start,
    events_for_tariff,
    format_event_dates,
    format_event_time,
    mark_task_started,
    month_list_events,
    neighbor_digests_for_student,
    task_done_for_user,
    task_status,
)
from app.services.tz import today_msk, now_msk
from app.services.upload_validation import read_image_uploads
from app.services.utils import compress_image
from app.services.video_catalog import get_published_video
from app.services.video_progress import get_video_progress, watch_threshold_seconds
from app.services.video_watch_events import (
    REFUSAL_BELOW_THRESHOLD,
    REFUSAL_COMPLETED_BEFORE_BLOCK,
    REFUSAL_NO_DURATION,
    REFUSAL_NO_VIDEO,
    REFUSAL_NOT_STARTED,
    record as record_watch_event,
    refusal_event,
    refusal_reason,
)
from app.services.video_topics import accessible_topic_ids
from app.tmpl import format_rich_text, templates

log = logging.getLogger(__name__)

router = APIRouter(prefix="/cabinet")


_NO_DEADLINE = datetime.max.replace(tzinfo=timezone.utc)


def _deadline_sort_key(entry: dict) -> datetime:
    due_at = entry["task"].due_at
    if due_at is not None:
        return due_at if due_at.tzinfo else due_at.replace(tzinfo=timezone.utc)
    return entry["cycle_deadline"] or _NO_DEADLINE


@router.get("/tracker", response_class=HTMLResponse)
def cabinet_tracker(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    today = today_msk()
    week_monday = week_start(today)
    _, week_end = day_bounds(week_monday + timedelta(days=6))

    # include_undated=True (10.09.2026): задания внутри цикла заводятся без
    # даты, накопленный долг «Личного трекера» должен видеть и их, как и
    # датные задания старого календаря.
    entries = accessible_task_entries(
        db, user["user_id"], start=None, end=week_end, include_undated=True,
    )
    # Прошлое и текущее — да, будущее — нет (владелец 06.10.2026: «показываем
    # все долги… не показывать будущий этап, который ещё не доступен»).
    # Текущий период — тот, где ученик стоит в ленте; ни одного начавшегося
    # цикла — сужать не по чему.
    period = current_period(db, user["user_id"], today)
    if period is not None:
        entries = entries_up_to_period(db, entries, period)
    # И только то, что открылось по дате (владелец 06.10.2026): не начавшийся
    # цикл и задание с датой открытия впереди не видны. Цикл, запертый долгом,
    # виден — это тоже долг; будущий этап должника отсекает рамка периода.
    entries = entries_open_to_student(db, entries, today)

    # Дайджест месяца — вкладка рядом с задачами (решение владельца 17.09.2026,
    # отменяет «первый блок на экране» от 22.08).
    digest = active_digest_for_student(db, user["user_id"], year=today.year, month=today.month)
    # Края сетки — из его же дайджестов соседних месяцев (владелец
    # 05.10.2026): 1 ноября из октябрьского видно и в ноябре, и наоборот.
    digest_events = (
        digest_events_with_edges(
            db, digest, neighbor_digests_for_student(db, user["user_id"], digest)
        )
        if digest is not None else []
    )
    # Событие с тарифами видит только ученик этих тарифов (созвон 30.09.2026,
    # владелец 01.10.2026). Сотрудник, открывший трекер под собой, видит все —
    # тарифа у него нет, а проверять расписание ему нужно целиком.
    if user.get("role_rank", 0) <= STUDENT_ROLE_RANK:
        digest_events = events_for_tariff(db, digest_events, user.get("tariff"))
    goal = active_goal_for_student(db, user["user_id"], today=today)
    digest_days = (
        digest_calendar(digest, digest_events, today=today) if digest is not None else []
    )

    # Ближайший срок сверху (владелец 06.10.2026): у задания цикла срок —
    # срок цикла, у датного — его `due_at`. Сортировка устойчивая, внутри
    # одного срока остаётся порядок программы; без срока — в конце.
    overdue = sorted(
        (e for e in entries if e["status"] == "overdue"), key=_deadline_sort_key
    )
    upcoming = sorted(
        (e for e in entries if e["status"] == "upcoming"), key=_deadline_sort_key
    )
    # Сделанное показываем только за эту неделю — иначе список рос бы вечно
    # закрытыми делами месячной давности, которые уже никому не интересны.
    #
    # Отбор по дате закрытия, не по дате дедлайна: закрытый сегодня долг
    # прошлой недели должен остаться на экране. Раньше здесь стояло
    # `e["day"] >= week_monday`, и такая задача исчезала совсем — из
    # «Просрочено» её выводил статус, в «Сделано» не пускал старый дедлайн.
    # Ученик жал галочку, обновлял страницу и не находил ни подтверждения,
    # ни задачи. Закрытые без отметки времени (до появления колонки) —
    # показываем, потерять их хуже, чем показать лишнее.
    done = [
        e for e in entries
        if e["status"] == "done"
        and (e["completed_on"] is None or e["completed_on"] >= week_monday)
    ]
    learning_task_ids = current_feed_task_ids(
        db, user_id=user["user_id"], today=today
    )
    # В каком цикле задание (владелец 06.10.2026: «показать, в каком цикле
    # долг»): подпись цикла — общая `cycle_label`, у задания прямо на этапе —
    # название этапа.
    cycle_labels: dict[int, str] = {}
    for entry in overdue + upcoming + done:
        topic_id = entry["task"].topic_id
        if topic_id is None or topic_id in cycle_labels:
            continue
        topic = db.get(LearningTopic, topic_id)
        if topic is None:
            continue
        if topic.kind == TOPIC_KIND_WEEK:
            cycle_labels[topic_id] = cycle_label(db, topic)
        elif topic.kind == TOPIC_KIND_STAGE and topic.title:
            cycle_labels[topic_id] = topic.title
    # Куда идти досдавать (владелец 06.10.2026: «все долги из предобучения
    # нужно показать, чтобы могли досдать»). Задание текущей ленты — в ленту,
    # как раньше; долг другого открытого цикла — в его ленту (`?cycle=`, тот же
    # вход, что у карусели); цикл, запертый долгом, — без кнопки, с подписью,
    # какой цикл закрыть первым. Отменяет решение «старые долги без кнопки
    # перехода» (`cabinet_learning.py`). Архивный цикл — только просмотр,
    # кнопку туда не даём.
    debt = cycle_debt(db, user["user_id"], today)
    locked_cycle_ids_ = {topic.id for topic in debt["locked"]} if debt else set()
    debt_cycle_label = cycle_label(db, debt["cycle"]) if debt else None
    go_urls: dict[int, str] = {}
    locked_task_ids: set[int] = set()
    for entry in overdue + upcoming:
        task = entry["task"]
        if task.id in learning_task_ids:
            go_urls[task.id] = f"/cabinet/learning?task={task.id}#learning-task-{task.id}"
        elif task.topic_id in locked_cycle_ids_:
            locked_task_ids.add(task.id)
        elif task.topic_id is not None and not task_is_archived_for_user(
            db, user["user_id"], task, today
        ):
            topic = db.get(LearningTopic, task.topic_id)
            if topic is not None and topic.kind == TOPIC_KIND_WEEK:
                go_urls[task.id] = (
                    f"/cabinet/learning?cycle={topic.id}#learning-task-{task.id}"
                )
    for entry in done:
        task = entry["task"]
        if task.id in learning_task_ids:
            go_urls[task.id] = f"/cabinet/learning?task={task.id}#learning-task-{task.id}"

    # Тип задания по содержимому (владелец 06.10.2026): по блокам, которые
    # видит этот ученик, правило — `task_blocks.task_content_label`.
    shown = overdue + upcoming + done
    blocks_by_task = get_task_blocks_for_tasks(db, [e["task"].id for e in shown])
    viewer = BlockViewer(db, user_id=user["user_id"], tariff=user.get("tariff"))
    content_labels: dict[int, str] = {}
    for task_id, task_blocks in blocks_by_task.items():
        label = task_content_label(
            visible_blocks_for_student(db, task_blocks, viewer=viewer)
        )
        if label:
            content_labels[task_id] = label

    # Средний балл точки А — под баллами Р/К в шапке (владелец 04.10.2026).
    # Только у разобранного ученика: уровень сообщается уведомлением ровно в
    # этот момент (`point_a.maybe_notify_point_a_level`), а промежуточное
    # среднее по части плашек выглядело бы итогом и прыгало бы с каждой новой
    # оценкой.
    student = db.get(User, user["user_id"])
    point_a = student_point_a(db, student, with_images=False) if student is not None else None
    point_a_average = point_a.average if point_a is not None and point_a.is_done else None

    return templates.TemplateResponse(request, "cabinet_tracker.html", {
        "request": request,
        "user": user,
        "overdue": overdue,
        "upcoming": upcoming,
        "done": done,
        "learning_task_ids": learning_task_ids,
        "cycle_labels": cycle_labels,
        "go_urls": go_urls,
        "locked_task_ids": locked_task_ids,
        "debt_cycle_label": debt_cycle_label,
        "content_labels": content_labels,
        "digest": digest,
        "digest_events": month_list_events(digest, digest_events) if digest is not None else [],
        # Заголовок «Сентябрь · тема месяца» и сетка месяца с цветными метками
        # над списком (вернулась 01.10.2026, отменяет «календарь не нужен» от
        # 17.09). Сетку строит общая month_days, та же, что у преподавателя.
        "digest_heading": digest_heading(digest) if digest is not None else None,
        "digest_days": digest_days,
        # Окно дня по тапу на число (владелец 05.10.2026).
        "digest_day_events": digest_day_events(digest_days),
        "digest_weekday_labels": WEEKDAY_LABELS,
        # Срок цикла в строке задания — тот же вид, что в шапке цикла
        # («до 12.10 в 23:59», `cycle_feed.deadline_view`).
        "deadline_view": deadline_view,
        "format_event_dates": format_event_dates,
        "format_event_time": format_event_time,
        "goal": goal,
        # Красное предупреждение (решение владельца 23.08, гейт «блок → неделя
        # → месяц»): ученик застрял на прошлой неделе, а не идёт по текущей.
        "is_behind_schedule": effective_week_start(db, user["user_id"], today) < week_monday,
        "active_tab": "tracker",
        "avg_score_by_subject": avg_score_by_subject_all_time(db, user["user_id"]),
        "point_a_average": point_a_average,
        # Плашка «ты осваиваешь N уровень программы» рядом с тарифом (владелец
        # 05.10.2026). Уровень считает та же `point_a_level`, что и уведомление,
        # и появляется он в тот же момент — когда точка А разобрана целиком.
        "point_a_level": point_a_level(point_a_average) if point_a_average is not None else None,
    })


@router.post("/tracker/tasks/{task_id}/toggle")
def cabinet_tracker_toggle(
    task_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    task = db.get(TrackerTask, task_id)
    if task is None or task.deleted_at is not None or not task.is_published:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    # Домашка и пробник закрываются только фактом сдачи (тариф без обратной
    # связи) или кнопкой куратора «Принять работу»/«Закрыть цикл» (тариф с
    # обратной связью, решение владельца 23.08) — ручная отметка была бы
    # обходом гейта в одно нажатие.
    if task.kind in (ITEM_HOMEWORK, ITEM_MOCK_EXAM):
        raise HTTPException(status_code=403, detail="Эта задача закрывается автоматически")

    # Доступ и запрет «вперёд нельзя» — общей функцией, без своей копии
    # условия (до 30.09.2026 здесь была копия, и запрет её не накрыл бы).
    _accessible_task_or_404(db, user["user_id"], task_id)
    if task_is_archived_for_user(db, user["user_id"], task, today_msk()):
        raise HTTPException(
            status_code=403, detail="Цикл пройден — можно только посмотреть свои ответы"
        )

    # Гейт «нельзя закрыть, пока не отвечены вопросы» — общей функцией: по ней
    # же лента рисует кнопку выключенной, своей копии условия здесь нет.
    blocker = task_completion_blocker(
        db, task_id=task_id, user_id=user["user_id"], user_tariff=user.get("tariff")
    )
    if blocker:
        raise HTTPException(status_code=409, detail=blocker)

    state = (
        db.query(TrackerTaskState)
        .filter(
            TrackerTaskState.task_id == task_id,
            TrackerTaskState.user_id == user["user_id"],
        )
        .one_or_none()
    )
    if state is None:
        state = TrackerTaskState(task_id=task_id, user_id=user["user_id"], status=STATUS_OPEN)
        db.add(state)

    # Отметка одноразовая, назад её снять нельзя (решение владельца
    # 26.08.2026): ученик должен быть уверен перед нажатием, а не полагаться
    # на то, что галочку можно снять и поставить заново. Повторный вызов на
    # уже закрытой задаче — не ошибка, а no-op: фронтенд блокирует кнопку
    # после первой отметки, но сам эндпоинт не должен откатывать состояние,
    # даже если запрос всё же прилетит повторно.
    if state.status != STATUS_DONE:
        state.status = STATUS_DONE
        state.completed_at = now_msk()
        state.completed_by_id = user["user_id"]
        db.commit()

    return JSONResponse({"status": task_status(task, state, now=now_msk())})


def _accessible_task_or_404(db: DBSession, user_id: int, task_id: int) -> TrackerTask:
    task = db.get(TrackerTask, task_id)
    if task is None or task.deleted_at is not None or not task.is_published:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    topic_ids = accessible_topic_ids(db, user_id)
    task_ids = accessible_task_ids(db, user_id)
    accessible = (task.topic_id is not None and task.topic_id in topic_ids) or (
        task.topic_id is None and task.id in task_ids
    )
    if not accessible:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    # Вперёд нельзя (владелец 30.09.2026): задание цикла после долга не
    # открывается и не отмечается, пока долг не закрыт. Здесь, а не в каждом
    # роуте, — через эту функцию идут и чтение блоков, и кружки, и кнопка.
    if task_is_locked_for_user(db, user_id, task, today_msk()):
        raise HTTPException(
            status_code=403, detail="Сначала закрой предыдущий цикл – этот пока заперт"
        )
    return task


def _writable_task_or_404(
    db: DBSession, user_id: int, task_id: int, *, revision_block_id: int | None = None,
) -> TrackerTask:
    """Как `_accessible_task_or_404`, плюс отказ, если задача лежит в
    архивном цикле ученика (владелец 24.09.2026, Этапы: пройденный цикл —
    только просмотр). Читающие эндпоинты эту обёртку не зовут и звать не
    должны — ученик обязан видеть свои старые ответы в архиве, только не
    менять их.
    """
    task = _accessible_task_or_404(db, user_id, task_id)
    returned = (
        get_task_block_submission(db, block_id=revision_block_id, user_id=user_id)
        if revision_block_id is not None else None
    )
    if task_is_archived_for_user(db, user_id, task, today_msk()) and not (
        returned is not None and returned.needs_revision
    ):
        raise HTTPException(
            status_code=403, detail="Цикл пройден — можно только посмотреть свои ответы"
        )
    return task


def _is_task_done(db: DBSession, task_id: int, user_id: int) -> bool:
    return task_done_for_user(db, task_id, user_id)


# ── GET /cabinet/tracker/tasks/{id}/blocks ───────────────────────────────────

def _portfolio_block_done(db: DBSession, block, user_id: int) -> bool:
    """Закрыт ли блок «Загрузить портфолио» — по факту загрузки работы «До».

    Отсчёт от даты открытия блока, иначе от даты открытия задания, иначе от
    начала дня задания: прошлогодняя работа не должна закрывать сегодняшний
    шаг (владелец 03.09.2026 — «портфолио, которое именно 18 числа»).

    Считаем только `before` и только успешные загрузки (владелец 09.09.2026:
    «по этой кнопке работы загружаются в ДО»). Раньше подходила любая работа
    любого типа: сданный пробник закрывал блок предобучения, а неудачная
    загрузка — открывала ленту, хотя файла в хранилище нет.
    """
    state = get_task_block_state(db, block_id=block.id, user_id=user_id)
    if state is not None and state.status == STATUS_DONE:
        return True
    if block.portfolio_window_hours and (state is None or state.started_at is None):
        return False
    task = db.get(TrackerTask, block.task_id)
    since = (
        state.started_at
        if block.portfolio_window_hours and state is not None and state.started_at
        else block.opens_at or (task.starts_at if task else None)
    )
    if since is None and task is not None and task.due_at is not None:
        since = day_bounds(msk_date(task.due_at))[0]
    if since is None:
        return False
    return (
        db.query(Work.id)
        .filter(
            Work.user_id == user_id,
            Work.work_type == WORK_TYPE_BEFORE,
            Work.status == "success",
            Work.created_at >= since,
        )
        .first()
        is not None
    )


def _video_block_watched(db: DBSession, block, user_id: int) -> bool:
    """Досмотрел ли ученик ролик видео-блока — по последнему подтверждённому просмотру,
    той же проверке антиперемотки, что уже стоит на видео-задаче целиком
    (`api/video.py::_save_progress`: сервер сам решает «досмотрел», клиенту не
    верим). «Просмотрено» — это только допуск к кнопке ниже, не сама отметка
    выполнения: блок закрывает ученик явным кликом (владелец 12.09.2026:
    «кружок нужно отметить, но проверяем, просмотрено видео или нет» — кто
    ставит отметку и кто её проверяет, две разные роли).

    `completed_at` хранит первый просмотр для истории, а `last_completed_at` — последний
    подтверждённый полный просмотр. Для конкретного блока требуем просмотр после
    `block.created_at`. Если тот же ролик позже добавят в
    другое занятие, старый просмотр не откроет новый кружок. Правка соседних полей
    сохраняет id и created_at блока, поэтому уже засчитанный просмотр от обычного
    редактирования задания не слетает.
    """
    if not block.video_id:
        return False
    video = db.get(LearningVideo, block.video_id)
    if video is None:
        return False
    progress = get_video_progress(db, user_id=user_id, video_id=video.bunny_video_id)
    if progress is None or block.created_at is None:
        return False
    completed_at = progress.last_completed_at or progress.completed_at
    if completed_at is None:
        return False
    block_created_at = block.created_at
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    if block_created_at.tzinfo is None:
        block_created_at = block_created_at.replace(tzinfo=timezone.utc)
    return completed_at >= block_created_at


def _video_watch_refusal(db: DBSession, block, user_id: int) -> str:
    """Почему кружок видео-блока не поставлен — строка для лога (владелец
    06.10.2026) и строка `VideoWatchEvent` для статистики карточки (07.10.2026).
    До этого отказ уходил ученику кодом `not_watched` и нигде не оставался: на
    жалобу «смотрела до конца, а не засчитало» ответить было нечем, кроме
    гипотез. Причину выбирает `video_watch_events.refusal_reason` — те же
    условия, что у `_video_block_watched`."""
    video = db.get(LearningVideo, block.video_id) if block.video_id else None
    progress = (
        get_video_progress(db, user_id=user_id, video_id=video.bunny_video_id)
        if video is not None else None
    )
    duration = video.duration_seconds if video is not None else None
    reason = refusal_reason(progress, video_exists=video is not None, duration_seconds=duration)
    if video is not None:
        record_watch_event(db, refusal_event(
            progress, user_id=user_id, video_id=video.bunny_video_id,
            block_id=block.id, reason=reason, duration_seconds=duration,
        ))
    if reason == REFUSAL_NO_VIDEO:
        return "ролика нет"
    if reason == REFUSAL_NOT_STARTED:
        return "ролик не запускался"
    numbers = (
        f"позиция={progress.position_seconds:.0f} | просмотрено={progress.covered_seconds:.0f}"
        f" | длительность={duration or 0:.0f}"
    )
    if reason == REFUSAL_COMPLETED_BEFORE_BLOCK:
        # Засчитан раньше, чем блок появился: в новом занятии нужен новый проход.
        return "засчитан до создания блока, нужен новый проход | " + numbers
    if reason == REFUSAL_NO_DURATION:
        return "у ролика нет длительности | " + numbers
    threshold = watch_threshold_seconds(duration)
    if reason == REFUSAL_BELOW_THRESHOLD:
        return f"не досмотрел до порога {threshold:.0f} | " + numbers
    return f"дошёл до конца, но пропустил {threshold - progress.covered_seconds:.0f} с | " + numbers


# Контроль просмотра видео. Выключался владельцем 19.09.2026: плеер Bunny не
# грузился у учеников без VPN, и кружок обязательного блока нельзя было
# поставить. Причину сняли мостом `video.assaru.space` (включён всем 21.09,
# `BUNNY_PLAYER_PROXY_BASE`), правило зачёта поправили 24.09 (за 30 секунд до
# конца, ускорение засчитывается), а флаг вернуть забыли: с 19.09 по 05.10
# кружок поставлен без досмотра 1173 раза из 1884. Включён снова владельцем
# 05.10.2026. Пара к `autocloses` в `partials/task_action.html` — выключать
# только оба места вместе.
VIDEO_WATCH_CONTROL_ENABLED = True


def _video_block_requires_completion(
    db: DBSession, task: TrackerTask, block: TaskBlock, user: dict
) -> bool:
    """Нужно ли требовать просмотр видео для закрытия блока.

    Любой видео-блок, обязательный или нет (владелец 05.10.2026: «проверять
    все»). С 17.09 по 05.10 проверялся только блок, у которого обязательны и
    задание, и сам блок, — на проде это 5 видео-блоков из 36, остальные
    закрывались кружком без просмотра. Пробник исключён по-прежнему.

    Просмотр требуется, только если его можно сделать и засчитать: ролик
    отдаётся этому ученику (то же правило, что у плеера,
    `get_published_video`) и у него есть длительность (без неё
    `evaluate_watch` не засчитает никогда). Иначе блок без ролика, с удалённым
    или снятым с публикации роликом запер бы шаг навсегда.
    """
    if not VIDEO_WATCH_CONTROL_ENABLED or task.kind == ITEM_MOCK_EXAM:
        return False
    if not block.video_id:
        return False
    video = get_published_video(db, block.video_id, viewer=user)
    return bool(video is not None and getattr(video, "duration_seconds", None))


def _submission_payload(
    db: DBSession, task: TrackerTask, block, user_id: int,
    user_tariff: str | None = None,
    tariff_deadlines: dict | None = None,
    task_tariff_deadlines: dict | None = None,
) -> dict:
    """Что ученик уже сдал в этом блоке — общая часть «загрузки работ» и
    «работы на время»: у них одна механика приёма, разная только обёртка.

    `submit_deadline` — срок приёма работ словами, тот же формат, что у окна
    портфолио (владелец 27.09.2026). Пока срок не вышел, ученик читает «до
    27 сентября, 09:30». После срока первую сдачу примут опозданием
    (`late_allowed`), а у сданной работы `edit_reason` говорит, что срок
    истёк, и рендерер убирает кнопки замены и удаления (владелец 06.10.2026).
    """
    submission = get_task_block_submission(db, block_id=block.id, user_id=user_id)
    images = (
        list_task_block_submission_images(db, submission.id) if submission else []
    )
    from app.models.task_block_feedback import TaskBlockFeedback
    has_feedback = bool(
        submission and db.query(TaskBlockFeedback.id).filter(
            TaskBlockFeedback.submission_id == submission.id
        ).first()
    )
    submit_until = submit_deadline_for(
        block, task, user_tariff=user_tariff,
        block_overrides=tariff_deadlines,
        task_overrides=task_tariff_deadlines,
    )
    deadline_passed = False
    if submit_until is not None:
        aware = submit_until if submit_until.tzinfo else submit_until.replace(tzinfo=timezone.utc)
        deadline_passed = aware <= datetime.now(timezone.utc)
    return {
        "upload_endpoint": f"/cabinet/tracker/blocks/{block.id}/upload",
        "max_files": submission_photo_limit(block),
        # «Сколько фото сдать» (владелец 02.10.2026): экран пишет «нужно ровно
        # N», считает «загружено X из N» и вместо поштучного удаления даёт
        # «Заменить фото».
        "required_photos": block.required_photos,
        "needs_revision": bool(submission and submission.needs_revision),
        "submitted_files": [{"id": i.id, "url": i.image_s3_url} for i in images],
        "edit_reason": block_work_reason(
            db, task, block, submission,
            user_tariff=user_tariff, tariff_deadlines=tariff_deadlines,
            task_tariff_deadlines=task_tariff_deadlines,
        ),
        "submit_deadline": format_deadline_msk(submit_until) or None,
        # После срока первую сдачу примут опозданием (владелец 06.10.2026,
        # до этого с 30.09 — только контрольную), и подсказка это скажет.
        "late_allowed": late_first_submission(block, submission),
        "deadline_passed": deadline_passed,
        "delete_endpoint": f"/cabinet/tracker/blocks/{block.id}/images",
        "comment_endpoint": f"/cabinet/tracker/blocks/{block.id}/comment",
        "submitted_comment": submission.comment if submission else None,
        "reviewed": bool(submission and submission.reviewed_at),
        "review_comment": submission.review_comment if submission else None,
        "review_comment_html": (
            format_rich_text(submission.review_comment)
            if submission and submission.review_comment else None
        ),
        "score": float(submission.score) if submission and submission.score is not None else None,
        "feedback_url": (
            f"/cabinet/task-block-submissions/{submission.id}/feedback"
            if submission and (has_feedback or submission.score is not None) else None
        ),
    }


@router.get("/tracker/tasks/{task_id}/blocks")
def cabinet_tracker_task_blocks(
    task_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Содержимое элемента: блоки конструктора по порядку плюс уже
    сохранённые ответы ученика (владелец 31.08.2026, см. докстринг
    `app/models/task_block.py`).

    Заменил прежний `/quiz`. Отличие в поведении, о котором стоит помнить:
    мини-опрос показывался **после** закрытия задачи (он был рефлексией по
    факту сдачи), а блоки — это содержимое самой задачи, и ученику они видны
    сразу, иначе он не увидит ни видео, ни текста, ни фото, которые нужны,
    чтобы задачу выполнить.

    Ролик отдаётся идентификатором: плеер запрашивает подписанный embed через
    существующий `/cabinet/videos/{id}/embed`, ключи Bunny в браузер не
    попадают. Доступ к такому ролику считается по блокам
    (`video_catalog.is_video_accessible`).
    """
    task = _accessible_task_or_404(db, user["user_id"], task_id)

    task_done = _is_task_done(db, task_id, user["user_id"])
    # Скрытый вопрос появляется только после закрытия задания — и весь блок
    # целиком, а не одна форма ответа: до сдачи ученик о нём не знает.
    # Блок чужого тарифа не уходит ученику вообще — ни разметкой, ни этим JSON
    # (владелец 06.09.2026, доведено 28.09.2026): до этого лента рисовала его
    # каркас, а здесь приезжали название и `video_id`. Сам ролик и тогда не
    # отдавался (`video_catalog.is_video_accessible`), но ученик читал в ленте
    # имя чужого урока и ждал, что оно откроется.
    blocks = [
        b for b in visible_blocks_for_student(
            db, get_task_blocks(db, task_id),
            viewer=BlockViewer(db, user_id=user["user_id"], tariff=user.get("tariff")),
        )
        # Флаг бывает у вопроса и у шкалы опроса (`sync_blocks`), у остальных
        # типов он всегда False — проверять тип не нужно.
        if not (b.hidden_until_done and not task_done)
    ]

    # Статистика прохождения диагностики (владелец 24.09.2026): единственная
    # точка, где сервер видит, что ученик открыл диагностику, — дальше ответы
    # уходят одним запросом на последнем шаге мастера, без промежуточных
    # сохранений. По блокам, не по `task.kind`: диагностика может лежать и
    # внутри обычного «Задания».
    if any(b.is_diagnostic for b in blocks):
        mark_task_started(db, task_id=task_id, user_id=user["user_id"])
    portfolio_by_block = (
        {
            window.block_id: window
            for window in portfolio_windows(
                db,
                user_id=user["user_id"],
                user_tariff=user.get("tariff"),
                section=WORK_TYPE_BEFORE,
            )
        }
        if any(b.block_type == BLOCK_PORTFOLIO for b in blocks)
        else {}
    )
    # Сроки приёма работ по тарифам — одним запросом на всё задание, а не по
    # блоку: у задания их бывает несколько (владелец 27.09.2026).
    submit_deadlines = get_task_block_submit_deadlines(db, [b.id for b in blocks])
    # Срок задания — запасной для блоков, которые своего не задали.
    task_submit_deadlines = get_task_level_submit_deadlines(db, [task_id]).get(task_id)
    # `question_blocks` отдаёт и вопросы, и шкалы навыков — у обоих есть
    # варианты и ответы ученика.
    questions = task_question_blocks(blocks)
    options = get_task_block_options(db, [b.id for b in questions])

    answers_map: dict[int, str] = {}
    selected: dict[int, set[int]] = {}
    selected_option_texts: dict[int, str] = {}
    response = get_task_block_response(db, task_id=task_id, user_id=user["user_id"])
    # «Ответил» — это когда закрыты все вопросы, а не когда просто появилась
    # строка заполнения (починка 07.09.2026). Раньше первая же отправка
    # запирала форму целиком: пропустил одно поле — вернуться некуда, а если
    # тот вопрос был обязательным, лента вставала намертво.
    answered_ids = (
        task_block_answered_ids(db, response_id=response.id) if response else set()
    )
    if response is not None:
        answers_map = get_task_block_answers_map(db, response_id=response.id)
        selected = get_task_block_selected_options(db, response_id=response.id)
        selected_option_texts = get_task_block_selected_option_texts(db, response_id=response.id)
    questions_left = [b for b in questions if b.id not in answered_ids]
    answered = bool(response) and not questions_left
    images = get_task_block_images(db, [b.id for b in blocks])
    # Вердикт отдаём, только когда ученик уже ответил. Иначе `is_correct` в
    # теле ответа подсказал бы верный вариант до отправки.
    verdict = (
        grade_task_blocks(db, blocks=blocks, response_id=response.id)
        if answered else None
    )
    correct_by_block = (
        {r["block_id"]: r["is_correct"] for r in verdict["results"]} if verdict else {}
    )
    profile_result = None
    has_diagnostic = any(b.is_diagnostic for b in blocks)
    if has_diagnostic:
        from app.services.archi_profile import result_for_answers
        profile_result = result_for_answers(db, task_id, user["user_id"])

    # Название/описание диагностики (владелец 24.09.2026, третий раунд) —
    # показываем один раз, перед первым вопросом диагностики, а не на каждом
    # (иначе «Вопрос 2» и дальше повторяли бы один и тот же заголовок).
    diagnostic_intro_shown = False

    def _deadline_reason(block):
        """Отказ по сроку для этого блока — с тарифом и сроком задания.

        Локальный, чтобы три ветки (вопрос, шкала, правила) не повторяли одни
        и те же пять аргументов: забыть один значит молча вернуться к общему
        сроку вместо тарифного. Первый ответ срок не запирает (владелец
        06.10.2026: «досдать свыше срока всегда можно»), только правку уже
        данного ответа.
        """
        return deadline_reason(
            task, block,
            user_tariff=user.get("tariff"),
            tariff_deadlines=submit_deadlines.get(block.id),
            task_tariff_deadlines=task_submit_deadlines,
            late_allowed=block.id not in answered_ids,
        )

    payload = []
    for block in blocks:
        item = {
            "id": block.id,
            "block_type": block.block_type,
            "title": block.title,
            "body": block.body,
            "body_html": format_rich_text(block.body) if block.body else None,
            # Запирается отдельный вопрос, а не форма разом: на пропущенный
            # ученик должен иметь возможность вернуться.
            "answered": block.id in answered_ids,
            # Срок сдачи — у блока любого типа (владелец 27.09.2026). Ученик
            # видит его и там, где срок ничего не запрещает: у видео и фото он
            # предупреждает, что отметка позже зачтётся опозданием.
            "submit_deadline": format_deadline_msk(submit_deadline_for(
                block, task,
                user_tariff=user.get("tariff"),
                block_overrides=submit_deadlines.get(block.id),
                task_overrides=task_submit_deadlines,
            )) or None,
        }
        # Опрос (владелец 30.09.2026): экран ученика собирает блоки с общим
        # ключом в один блок с мастером «Далее» (`lrnBlockRender.runPoll`).
        # Описание опроса лежит на первом его блоке (`sync_blocks`).
        if block.poll_key:
            item["poll_key"] = block.poll_key
            if block.poll_intro:
                item["poll_intro_html"] = format_rich_text(block.poll_intro)
        if block.is_diagnostic and not diagnostic_intro_shown and task.diagnostic_config:
            diagnostic_intro_shown = True
            intro_title = task.diagnostic_config.get("title")
            intro_body = task.diagnostic_config.get("intro")
            if intro_title:
                item["diagnostic_intro_title"] = intro_title
            if intro_body:
                item["diagnostic_intro_body_html"] = format_rich_text(intro_body)
        if block.block_type == BLOCK_VIDEO:
            item["video_id"] = block.video_id
            item["video_embed_endpoint"] = (
                f"/cabinet/videos/{block.video_id}/embed" if block.video_id else None
            )
            # Проверка просмотра — у любого видео-блока, обязательность блока
            # и задания на неё не влияет (владелец 05.10.2026). Кружок нужен
            # всем: блок входит в счётчик «Сделано N из M» ленты (18.09.2026).
            state = get_task_block_state(db, block_id=block.id, user_id=user["user_id"])
            item["done"] = bool(state and state.status == STATUS_DONE)
            item["requires_watch"] = _video_block_requires_completion(db, task, block, user)
            item["watched"] = (
                _video_block_watched(db, block, user["user_id"])
                if item["requires_watch"] else False
            )
            item["confirm_endpoint"] = f"/cabinet/tracker/blocks/{block.id}/watched"
        elif block.block_type == BLOCK_PHOTO:
            item["images"] = [
                {"url": i.image_s3_url} for i in images.get(block.id, [])
            ]
            # Кружок выполнения по аналогии с видео (владелец 12.09.2026): у
            # фото нет сигнала вроде `VideoProgress`, поэтому эндпоинт просто
            # закрывает блок по клику, без встречной проверки.
            state = get_task_block_state(db, block_id=block.id, user_id=user["user_id"])
            item["done"] = bool(state and state.status == STATUS_DONE)
            item["confirm_endpoint"] = f"/cabinet/tracker/blocks/{block.id}/done"
        elif block.block_type == BLOCK_MEDIA:
            # Голосовое / кружок преподавателя (владелец 25.09.2026). Закрывается
            # тем же кружком «Выполнено», что фото: иначе обязательный блок
            # навсегда запер бы ленту ниже.
            item["media_kind"] = block.media_kind
            item["media_url"] = block.media_s3_url
            state = get_task_block_state(db, block_id=block.id, user_id=user["user_id"])
            item["done"] = bool(state and state.status == STATUS_DONE)
            item["confirm_endpoint"] = f"/cabinet/tracker/blocks/{block.id}/done"
        elif block.block_type == BLOCK_LINK:
            # Адрес ссылки ученику не уходит: кнопка ведёт на сервер, и тот
            # пускает дальше только того, кому шаг открыт (`go_link_block`).
            item["go_url"] = f"/cabinet/tracker/blocks/{block.id}/go"
        elif block.block_type == BLOCK_COMPARE:
            # Сравнение работ (Лиза 27.09.2026). Выбор преподавателя уходит
            # только вместе с ответом ученика — до этого `is_pick` наружу не
            # отдаём, как `is_correct` у вопроса.
            compare_images = images.get(block.id, [])
            item["images"] = [{"url": i.image_s3_url} for i in compare_images]
            chosen_url = answers_map.get(block.id) or None
            state = get_task_block_state(db, block_id=block.id, user_id=user["user_id"])
            item["done"] = bool(state and state.status == STATUS_DONE)
            item["chosen_url"] = chosen_url
            if chosen_url:
                # Совпадение считаем при чтении по текущей отметке: если
                # преподаватель передумал, ученик видит актуальный вердикт.
                pick_url = compare_pick_url(compare_images)
                item["pick_url"] = pick_url
                item["matched"] = chosen_url == pick_url
                item["edit_reason"] = None
                item["submit_endpoint"] = None
            else:
                item["edit_reason"] = _deadline_reason(block)
                item["submit_endpoint"] = (
                    None if item["edit_reason"]
                    else f"/cabinet/tracker/blocks/{block.id}/compare"
                )
                # Пара, на которой ученик остановился: после перезагрузки —
                # туда же (владелец 28.09.2026). Турнир ведёт сервер.
                progress = compare_progress(
                    db, block_id=block.id, user_id=user["user_id"], images=compare_images
                )
                item["progress"] = (
                    {key: progress[key] for key in ("champion_url", "challenger_url", "step", "total")}
                    if progress else None
                )
        elif block.block_type == BLOCK_SCALE:
            item["edit_reason"] = _deadline_reason(block) or (
                "Преподаватель уже проверил ответ."
                if response and db.query(TaskBlockAnswer.id).filter(
                    TaskBlockAnswer.response_id == response.id,
                    TaskBlockAnswer.block_id == block.id,
                    TaskBlockAnswer.reviewed_at.isnot(None),
                ).first() else None
            )
            # Диагностика навыков: варианты — навыки, ответ — оценка каждому.
            item["scale_max"] = SCALE_MAX
            item["scale_min"] = SCALE_MIN
            # Своя кнопка «Сохранить» у блока (владелец 12.09.2026) — эндпоинт
            # тот же, что у общей формы «Отправить ответы» внизу задания:
            # `submit_cabinet_tracker_task_blocks` принимает ответы частями,
            # отправка одного блока не требует и не трогает остальные.
            item["submit_endpoint"] = f"/cabinet/tracker/tasks/{task_id}/blocks"
            item["options"] = [
                {
                    "id": o.id,
                    "text": o.text,
                    "description": o.description,
                    "description_html": (
                        format_rich_text(o.description) if o.description else None
                    ),
                    "scale_min_label": o.scale_min_label,
                    "scale_max_label": o.scale_max_label,
                }
                for o in options.get(block.id, [])
            ]
            item["answer_option_texts"] = {
                option_id: text
                for option_id, text in selected_option_texts.items()
            }
        elif block.block_type == BLOCK_TIMED:
            state = get_task_block_state(db, block_id=block.id, user_id=user["user_id"])
            item["time_limit_minutes"] = block.time_limit_minutes
            item["started_at"] = (
                state.started_at.isoformat() if state and state.started_at else None
            )
            item["done"] = bool(state and state.status == STATUS_DONE)
            # Условие контрольной и фото к нему — до «Начать работу» не отдаём вовсе
            # (владелец 02.10.2026): иначе ученик читает задание, обдумывает
            # его и только потом запускает таймер. Прятать в браузере мало —
            # текст остался бы в ответе сервера.
            if not item["started_at"] and not item["done"]:
                item["body"] = None
                item["body_html"] = None
                item["body_hidden"] = True
            else:
                # Фото к условию — по тому же правилу, что и текст.
                item["images"] = [
                    {"url": i.image_s3_url} for i in images.get(block.id, [])
                ]
            item["overrun"] = task_block_timed_overrun(block, state)
            # Обратный отсчёт на экране и отметка «после срока» (владелец
            # 30.09.2026): остаток считает сервер, браузер только тикает.
            item["time_left_seconds"] = task_block_timed_seconds_left(block, state)
            item["late"] = task_block_completed_after_deadline(
                block, task, state, user_tariff=user.get("tariff"),
                block_overrides=submit_deadlines.get(block.id),
                task_overrides=task_submit_deadlines,
            )
            item["start_endpoint"] = f"/cabinet/tracker/blocks/{block.id}/start"
            item.update(_submission_payload(
                db, task, block, user["user_id"],
                user_tariff=user.get("tariff"),
                tariff_deadlines=submit_deadlines.get(block.id),
                task_tariff_deadlines=task_submit_deadlines,
            ))
        elif block.block_type == BLOCK_UPLOAD:
            # Работы грузятся здесь же, ученик никуда не уходит (владелец
            # 07.09.2026). `done` берём из состояния блока: его ставит сам
            # роут загрузки, а не пересчёт по портфолио.
            state = get_task_block_state(db, block_id=block.id, user_id=user["user_id"])
            item["done"] = bool(state and state.status == STATUS_DONE)
            item.update(_submission_payload(
                db, task, block, user["user_id"],
                user_tariff=user.get("tariff"),
                tariff_deadlines=submit_deadlines.get(block.id),
                task_tariff_deadlines=task_submit_deadlines,
            ))
        elif block.block_type == BLOCK_PHOTO_UPLOAD:
            # Фото + сдача работы (владелец 12.09.2026): фото-задание — та же
            # галерея, что у BLOCK_PHOTO, приём результата — тот же приём, что
            # у BLOCK_UPLOAD. `done` берёт роут загрузки, отдельного кружка
            # подтверждения у фото здесь нет — блок закрывает сама сдача.
            item["images"] = [
                {"url": i.image_s3_url} for i in images.get(block.id, [])
            ]
            state = get_task_block_state(db, block_id=block.id, user_id=user["user_id"])
            item["done"] = bool(state and state.status == STATUS_DONE)
            item.update(_submission_payload(
                db, task, block, user["user_id"],
                user_tariff=user.get("tariff"),
                tariff_deadlines=submit_deadlines.get(block.id),
                task_tariff_deadlines=task_submit_deadlines,
            ))
        elif block.block_type == BLOCK_PORTFOLIO:
            # Ведём на существующий экран загрузки работ, своего у блока нет.
            # Всегда в раздел «До» (владелец 09.09.2026: «по этой кнопке работы
            # загружаются в ДО»). Без явного `section` экран выбирает раздел
            # сам по `portfolio_do_completed`, и ученик, уже грузивший работы,
            # попадал в «После». Раздел доезжает и до отправки: `upload.html`
            # кладёт `section` скрытым полем формы.
            # `block` — подсказка экрану загрузки, какое окно ученик открыл
            # (18.09.2026): при двух открытых окнах он покажет срок того, с
            # кнопки которого пришли. Прав ссылка не даёт — окно проверяется
            # на сервере целиком, см. `services/portfolio_window.py`.
            window = portfolio_by_block.get(block.id)
            item["upload_url"] = f"/upload?section=before&block={block.id}"
            item["window_open"] = window.is_open if window is not None else True
            item["window_deadline"] = (
                format_deadline_msk(window.closes_at)
                if window is not None and window.closes_at is not None else None
            )
            item["done"] = _portfolio_block_done(db, block, user["user_id"])
            # Видеоинструкция и примеры над кнопкой (владелец 17.09.2026).
            # Оба необязательны; закрытие шага от них не зависит — только
            # загрузка работы, кружка «просмотрено» у видео здесь нет.
            item["video_embed_endpoint"] = (
                f"/cabinet/videos/{block.video_id}/embed" if block.video_id else None
            )
            item["images"] = [
                {"url": i.image_s3_url} for i in images.get(block.id, [])
            ]
        elif block.block_type == BLOCK_QUESTION:
            item["question_type"] = block.question_type
            item["is_archi_profile"] = block.is_diagnostic
            item["options"] = [
                # `is_correct` наружу не отдаём: ученик не должен видеть
                # правильный ответ в теле ответа сервера. `requires_text`
                # нужен фронту, чтобы понять, под каким вариантом раскрывать
                # поле свободного текста (владелец 05.09.2026).
                {
                    "id": o.id, "text": o.text,
                    # Тот же приём, что у `body_html` выше: преподаватель мог
                    # стилизовать текст варианта (диагностика АРХИ-ПРОФИЛЯ) —
                    # рендерер ученика ждёт готовый HTML, не сырую разметку.
                    "text_html": format_rich_text(o.text) if o.text else None,
                    "description": o.description if block.is_diagnostic else None,
                    "requires_text": o.requires_text,
                }
                for o in options.get(block.id, [])
            ]
            item["answer_text"] = answers_map.get(block.id, "")
            item["answer_option_ids"] = sorted(selected.get(block.id, set()))
            item["answer_option_texts"] = {
                option_id: text
                for option_id, text in selected_option_texts.items()
                if option_id in selected.get(block.id, set())
            }
            item["is_correct"] = correct_by_block.get(block.id)
            item["edit_reason"] = (
                _deadline_reason(block)
                or (
                    (
                        "Этот ответ уже проверен системой."
                        if not block.is_diagnostic
                        and any(o.is_correct for o in options.get(block.id, []))
                        else "Ответ сохранён."
                    )
                    if block.question_type != QUESTION_TEXT and block.id in answered_ids
                    else None
                )
                or ("Преподаватель уже проверил ответ." if response and db.query(TaskBlockAnswer.id).filter(
                    TaskBlockAnswer.response_id == response.id,
                    TaskBlockAnswer.block_id == block.id,
                    TaskBlockAnswer.reviewed_at.isnot(None),
                ).first() else None)
            )
        elif block.block_type == BLOCK_RULES:
            item["edit_reason"] = _deadline_reason(block) or (
                "Согласие с правилами уже сохранено." if block.id in answered_ids else None
            )
            # Галочки уходят сами, как только отмечены все (владелец
            # 04.10.2026: «после проставления галочки… считать задание
            # выполненным и открывать следующее»), — без общей кнопки
            # «Сохранить ответы». Эндпоинт тот же, что у формы: ответы
            # принимаются частями. Нет адреса — нечего и слать: срок вышел или
            # согласие уже сохранено.
            item["submit_endpoint"] = (
                None if item["edit_reason"] else f"/cabinet/tracker/tasks/{task_id}/blocks"
            )
            # Правила школы: варианты — сами правила, `body` — текст согласия.
            # Отмеченные отдаём, чтобы уже закрытый блок открывался с
            # проставленными галочками, а не пустым.
            #
            # С 04.10.2026 пункт несёт текст, фото, видео или аудио (`kind`,
            # пусто — текст), а `text` — подпись у галочки под ним. Файлы —
            # те же публичные ссылки S3, что у блока «Голосовое / кружок».
            #
            # Подпись и текст пункта — готовым HTML из `format_rich_text`, как
            # у вариантов вопроса (владелец 04.10.2026: форматирование в полях
            # и блоках — от эталонной настройки). До этого подпись шла сырой
            # строкой, и `**жирный**` ученик видел со звёздочками.
            rule_options = options.get(block.id, [])
            rule_images = get_task_block_option_images(db, [o.id for o in rule_options])
            item["options"] = [
                {
                    "id": o.id,
                    "text": o.text,
                    "text_html": format_rich_text(o.text) if o.text else None,
                    "kind": o.content_kind,
                    "description_html": (
                        format_rich_text(o.description) if o.description else None
                    ),
                    "images": [{"url": i.image_s3_url} for i in rule_images.get(o.id, [])],
                    "media_url": o.media_s3_url,
                }
                for o in rule_options
            ]
            item["answer_option_ids"] = sorted(selected.get(block.id, set()))
        payload.append(item)

    editable_answers = [
        item for item in payload
        if item["block_type"] in (BLOCK_QUESTION, BLOCK_SCALE)
        and item.get("edit_reason") is None
        and (item["block_type"] == BLOCK_SCALE or item.get("question_type") == QUESTION_TEXT)
    ]
    return JSONResponse({
        "blocks": payload,
        "is_archi_profile": has_diagnostic,
        "questions_left_count": len(questions_left),
        "has_questions": bool(questions),
        # Одна попытка (владелец 31.08.2026): ответил — форма закрывается.
        # Иначе, увидев «неверно», можно было бы переотправить до победы, и
        # счёт перестал бы что-либо значить.
        "answered": answered,
        "submit_endpoint": (
            f"/cabinet/tracker/tasks/{task_id}/blocks"
            if questions_left or editable_answers else None
        ),
        "correct_count": verdict["correct_count"] if verdict else None,
        "gradable_count": verdict["gradable_count"] if verdict else None,
        "archi_profile": profile_result,
    })


class TrackerBlockAnswerItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: int = Field(ge=1)
    text: str | None = Field(default=None, max_length=2000)
    option_ids: list[int] = Field(default_factory=list, max_length=20)
    # Свободный текст под конкретным выбранным вариантом (владелец 05.09.2026:
    # «выбрал навык — сразу под ним раскрывается поле, почему»). Ключ —
    # option_id; сервис (`save_response`) сам игнорирует текст у вариантов
    # без `requires_text`, даже если он всё равно пришёл.
    option_texts: dict[int, str] = Field(default_factory=dict, max_length=20)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @field_validator("option_texts")
    @classmethod
    def clean_option_texts(cls, value: dict[int, str]) -> dict[int, str]:
        cleaned = {}
        for option_id, text in value.items():
            text = (text or "").strip()
            if text:
                cleaned[option_id] = text[:2000]
        return cleaned


class TrackerTaskBlocksSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[TrackerBlockAnswerItem] = Field(min_length=1, max_length=MAX_BLOCKS)


@router.get("/tracker/blocks/{block_id}/go")
def go_link_block(
    block_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Кнопка блока «Ссылка»: переход по ней через сервер.

    Владелец 17.08 и 03.10.2026: ссылка на занятие зашита в кнопку, чтобы
    ребёнок не скопировал её и не переслал. До 03.10.2026 в кнопке лежал сам
    адрес Zoom — долгое нажатие «Скопировать ссылку» уносило его в общий чат, и
    он открывал занятие тем, кто ничего не сдал. Теперь в кнопке адрес этого
    роута: пересланный, он не откроется без своего кабинета ученика и
    открытого в его ленте шага (`cycle_feed.block_step_is_open`).

    Это не замок: после перехода адрес встречи виден в браузере и в Zoom.
    Намеренную пересылку закрывает зал ожидания на стороне Zoom.
    """
    block = db.get(TaskBlock, block_id)
    if block is None or block.block_type != BLOCK_LINK or not block.url:
        raise HTTPException(status_code=404, detail="Ссылка не найдена")
    # Запертый долгом цикл — тот же отказ, что у закрытого шага: заглушка
    # с текстом `_accessible_task_or_404` вышла бы с заголовком «Аккаунт
    # заблокирован».
    try:
        task = _accessible_task_or_404(db, user["user_id"], block.task_id)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        raise HTTPException(status_code=403, detail=LINK_LOCKED_DETAIL) from exc
    if not block_step_is_open(
        db, user_id=user["user_id"], user_tariff=user.get("tariff"),
        task=task, block_id=block.id, today=today_msk(),
    ):
        raise HTTPException(status_code=403, detail=LINK_LOCKED_DETAIL)
    return RedirectResponse(
        block.url,
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/tracker/blocks/{block_id}/start", response_class=JSONResponse)
def start_timed_block_route(
    block_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Старт работы на время: отсчёт начинается по кнопке ученика.

    Время старта — предмет измерения, поэтому повторное нажатие его не
    сдвигает (`start_timed_block`).
    """
    block = db.get(TaskBlock, block_id)
    if block is None or block.block_type != BLOCK_TIMED:
        raise HTTPException(status_code=404, detail="Блок не найден")
    _writable_task_or_404(db, user["user_id"], block.task_id)
    state = start_task_timed_block(db, block=block, user_id=user["user_id"])
    db.commit()
    return JSONResponse({
        "ok": True,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "time_limit_minutes": block.time_limit_minutes,
    })


@router.post("/tracker/blocks/{block_id}/watched", response_class=JSONResponse)
def confirm_video_block_watched(
    block_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Ученик отмечает видео-блок выполненным кружком в углу карточки.

    Отметку ставит ученик кликом, а сервер дополнительно проверяет
    `VideoProgress` — у любого видео-блока, кроме пробника
    (`_video_block_requires_completion`).
    """
    block = db.get(TaskBlock, block_id)
    if block is None or block.block_type != BLOCK_VIDEO:
        raise HTTPException(status_code=404, detail="Блок не найден")
    # Без гейта архива (владелец 28.09.2026: этап закончился 27-го, а ученики
    # досматривают видео). Блок без сдачи работы отмечается и в пройденном
    # цикле; опоздание видно в статистике «до срока / после срока»
    # (`activity_stats`). Срок отметку не запирает ни у одного типа.
    task = _accessible_task_or_404(db, user["user_id"], block.task_id)
    if _video_block_requires_completion(db, task, block, user) and not _video_block_watched(
        db, block, user["user_id"]
    ):
        # Причину пишет и в базу (статистика карточки), поэтому вызов отдельно.
        refusal = _video_watch_refusal(db, block, user["user_id"])
        log.warning(
            "Видео не засчитано, кружок не поставлен | block=%s | user=%s | причина=%s",
            block_id, user["user_id"], refusal,
        )
        return JSONResponse({"ok": False, "error": "not_watched"}, status_code=409)
    close_task_block_for_user(db, block=block, user_id=user["user_id"], source="video_watched")
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/tracker/blocks/{block_id}/done", response_class=JSONResponse)
def confirm_photo_block_done(
    block_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Ученик отмечает фото-блок выполненным кружком в углу карточки.

    По аналогии с видео (см. `confirm_video_block_watched`), но без встречной
    проверки: у фото нет сигнала вроде `VideoProgress`, само содержимое блока
    доступно ученику сразу — отметка нужна только для трекинга прогресса.
    """
    block = db.get(TaskBlock, block_id)
    if block is None or block.block_type not in (BLOCK_PHOTO, BLOCK_MEDIA):
        raise HTTPException(status_code=404, detail="Блок не найден")
    # Без гейта архива — по той же причине, что у видео выше.
    _accessible_task_or_404(db, user["user_id"], block.task_id)
    source = "media_confirmed" if block.block_type == BLOCK_MEDIA else "photo_confirmed"
    close_task_block_for_user(db, block=block, user_id=user["user_id"], source=source)
    db.commit()
    return JSONResponse({"ok": True})


class CompareChoicePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_url: str = Field(min_length=1, max_length=500)
    # Номер пары, в которой ученик нажал: сервер отвергает устаревший.
    step: int | None = Field(default=None, ge=1)


@router.post("/tracker/blocks/{block_id}/compare", response_class=JSONResponse)
def submit_compare_choice(
    block_id: int,
    payload: CompareChoicePayload,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Выбор ученика в текущей паре блока «Сравнение работ» (Лиза 27.09.2026).

    Владелец 28.09.2026: каждое нажатие — сюда, выбор окончательный, пару
    ведёт сервер. Ответ — следующая пара или, после последней, вердикт.
    Повтор после итога — 409, работа не из текущей пары — 422. После срока —
    409 с тем же текстом, что у остальных блоков.
    """
    block = db.get(TaskBlock, block_id)
    if block is None or block.block_type != BLOCK_COMPARE:
        raise HTTPException(status_code=404, detail="Блок не найден")
    task = _writable_task_or_404(db, user["user_id"], block.task_id)
    # Срок — с тарифом ученика и сроком задания, как в ленте: без них
    # `deadline_reason` молча смотрел бы только на общий срок блока. Сравнение
    # идёт сюда, пока не закончено (повтор после итога отбивает
    # `save_compare_step`), — это первая сдача, срок её не запирает
    # (владелец 06.10.2026); запирает только закрытие блока.
    reason = deadline_reason(
        task, block,
        user_tariff=user.get("tariff"),
        tariff_deadlines=get_task_block_submit_deadlines(db, [block.id]).get(block.id),
        task_tariff_deadlines=get_task_level_submit_deadlines(db, [task.id]).get(task.id),
        late_allowed=True,
    )
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    try:
        result = save_compare_step(
            db, block=block, user_id=user["user_id"], image_url=payload.image_url,
            step=payload.step,
        )
        db.commit()
    except CompareChoiceError as error:
        db.rollback()
        raise HTTPException(status_code=409 if error.already else 422, detail=str(error))
    except IntegrityError:
        # Второй запрос на ту же пару (двойное нажатие, две вкладки).
        db.rollback()
        raise HTTPException(status_code=409, detail="Этот выбор уже сохранён. Обнови страницу")
    return JSONResponse({"ok": True, **result})


MAX_UPLOAD_FILE_SIZE = 10 * 1024 * 1024


@router.post("/tracker/blocks/{block_id}/upload", response_class=JSONResponse)
async def upload_task_block_work(
    block_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
    photos: list[UploadFile] = File(...),
    comment: str | None = Form(default=None),
    replace: bool = Form(default=False),
):
    """Приём работы прямо в блоке задания (владелец 07.09.2026).

    Контракт загрузки взят у домашки (`api/homework_submission.py`): та же
    валидация, то же сжатие, тот же отказ 502, когда S3 настроен, но не
    ответил. Разница одна — файл привязан к блоку, а не к постановке задачи,
    поэтому в одном задании может стоять несколько разных приёмов работ.

    Блок закрывается здесь же, а не пересчётом по портфолио: до этого любая
    загрузка на общем экране `/upload` закрывала блок, даже если ученик грузил
    совсем другое.
    """
    block = db.get(TaskBlock, block_id)
    if block is None or block.block_type not in SUBMISSION_BLOCK_TYPES:
        raise HTTPException(status_code=404, detail="Блок не найден")
    task = _writable_task_or_404(
        db, user["user_id"], block.task_id, revision_block_id=block.id,
    )

    # Каждый отказ пишется в лог. До 26.09.2026 их не было видно вовсе: в
    # журнале стоял только код ответа, а причина уезжала ученику в JSON — на
    # жалобу «пишет, что не удалось загрузить» ответить было нечем, кроме
    # гипотез. Теперь на одну жалобу хватает одной строки лога.
    def _refused(kind: str, detail: str) -> None:
        log.warning(
            "Отказ на загрузке работы | block=%s | user=%s | причина=%s | %s | файлов=%s",
            block_id, user["user_id"], kind, detail, len(photos or []),
        )

    submission = get_task_block_submission(db, block_id=block.id, user_id=user["user_id"])
    # Тариф обязателен: у блока может стоять свой срок приёма для этого
    # тарифа, и без него сработал бы общий (владелец 27.09.2026).
    reason = block_work_reason(
        db, task, block, submission, user_tariff=user.get("tariff"),
    )
    if reason:
        _refused("правка закрыта", reason)
        return JSONResponse({"ok": False, "error": reason}, status_code=409)
    submission = submission or get_or_create_task_block_submission(db, block=block, user_id=user["user_id"])
    existing = count_task_block_submission_images(db, submission.id)
    limit = submission_photo_limit(block)
    required = block.required_photos
    # «Заменить фото» (владелец 02.10.2026) — только при заданном числе: там
    # поштучное удаление закрыто, иначе при «ровно 1» не заменить ничего.
    # Новые фото встают вместо всех старых одним запросом, поэтому их ровно N.
    replacing = bool(replace and existing and (required or submission.needs_revision))
    if replacing:
        if required and len(photos or []) != required:
            _refused("замена не тем числом", f"нужно {required}")
            return JSONResponse(
                {"ok": False, "error": _required_count_error(required, len(photos or []))},
                status_code=422,
            )
        existing = 0
    if existing >= limit:
        _refused("лимит файлов", f"уже загружено {existing} из {limit}")
        error = (
            f"Работа уже сдана: загружено {existing} из {required}. "
            "Чтобы поменять фото, нажми «Заменить фото»"
            if required else
            f"Лимит файлов исчерпан: уже загружено {existing} из {limit}"
        )
        return JSONResponse({"ok": False, "error": error}, status_code=422)
    if required and len(photos or []) > limit - existing:
        _refused("больше нужного", f"выбрано {len(photos)}, можно ещё {limit - existing} из {required}")
        return JSONResponse(
            {"ok": False, "error": _required_count_error(required, len(photos), existing)},
            status_code=422,
        )
    files, err = await read_image_uploads(
        photos,
        max_files=limit - existing,
        max_size=MAX_UPLOAD_FILE_SIZE,
    )
    if err:
        _refused("валидация файлов", err)
        return JSONResponse({"ok": False, "error": err}, status_code=422)
    if replacing:
        delete_task_block_submission_images(db, submission.id)

    created = 0
    for filename, data in files:
        s3_path = s3_service.s3_path_task_block_submission(
            user["vk_id"], submission.id, filename, user.get("tariff") or "",
        )
        url = s3_service.upload_to_s3(s3_path, compress_image(data), "image/jpeg")
        if s3_service.is_configured() and not url:
            _refused("хранилище не ответило", f"{s3_path}, байт={len(data)}")
            return JSONResponse(
                {"ok": False, "error": "Ошибка загрузки в хранилище"}, status_code=502
            )
        add_task_block_submission_image(
            db, submission=submission, url=url or "", path=s3_path if url else None
        )
        created += 1

    # С заданным числом работа сдана только при N из N: неполная сдача не
    # закрывает блок и не попадает к проверяющему (владелец 02.10.2026).
    if created and is_submission_complete(block, existing + created):
        mark_task_block_submitted(db, submission=submission, comment=comment)
        # Закрываем блок фактом сдачи: работа приехала, лента едет дальше.
        # Проверка куратора на это не влияет — иначе ученик стоял бы в ленте,
        # пока куратор не дойдёт до его работы (то же правило, что у домашки
        # на тарифе без обратной связи).
        close_task_block_for_user(
            db, block=block, user_id=user["user_id"], source="submission"
        )
    elif created and comment is not None:
        # Описание, набранное к первой части фото, не должно теряться.
        submission.comment = comment.strip() or None
    db.commit()
    return JSONResponse({"ok": True, "created": created})


def _required_count_error(required: int, chosen: int, existing: int = 0) -> str:
    """Текст отказа, когда выбрано не столько фото, сколько нужно сдать."""
    if existing:
        return (
            f"Нужно ровно {required} фото, уже загружено {existing}. "
            f"Догрузи ещё {required - existing}, а выбрано {chosen}"
        )
    if required == 1:
        return f"Нужно ровно 1 фото, а выбрано {chosen}. Вся работа – на одном снимке"
    return f"Нужно ровно {required} фото, а выбрано {chosen}"


@router.post("/tracker/blocks/{block_id}/comment", response_class=JSONResponse)
def edit_task_block_comment(
    block_id: int, payload: dict,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    block = db.get(TaskBlock, block_id)
    if block is None or block.block_type not in SUBMISSION_BLOCK_TYPES:
        raise HTTPException(status_code=404, detail="Блок не найден")
    task = _writable_task_or_404(
        db, user["user_id"], block.task_id, revision_block_id=block.id,
    )
    submission = get_task_block_submission(db, block_id=block.id, user_id=user["user_id"])
    if submission is None or submission.submitted_at is None:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    # Тариф обязателен: у блока может стоять свой срок приёма для этого
    # тарифа, и без него сработал бы общий (владелец 27.09.2026).
    reason = block_work_reason(
        db, task, block, submission, user_tariff=user.get("tariff"),
    )
    if reason:
        return JSONResponse({"ok": False, "error": reason}, status_code=409)
    comment = payload.get("comment")
    if not isinstance(comment, str) or len(comment) > 2000:
        raise HTTPException(status_code=422, detail="Описание должно быть короче 2000 символов")
    submission.comment = comment.strip() or None
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/tracker/blocks/{block_id}/images/{image_id}/delete", response_class=JSONResponse)
def delete_task_block_image(
    block_id: int, image_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    block = db.get(TaskBlock, block_id)
    if block is None or block.block_type not in SUBMISSION_BLOCK_TYPES:
        raise HTTPException(status_code=404, detail="Блок не найден")
    task = _writable_task_or_404(
        db, user["user_id"], block.task_id, revision_block_id=block.id,
    )
    submission = get_task_block_submission(db, block_id=block.id, user_id=user["user_id"])
    if submission is None:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    # Тариф обязателен: у блока может стоять свой срок приёма для этого
    # тарифа, и без него сработал бы общий (владелец 27.09.2026).
    reason = block_work_reason(
        db, task, block, submission, user_tariff=user.get("tariff"),
    )
    if reason:
        return JSONResponse({"ok": False, "error": reason}, status_code=409)
    image = db.query(TaskBlockSubmissionImage).filter(
        TaskBlockSubmissionImage.id == image_id,
        TaskBlockSubmissionImage.submission_id == submission.id,
    ).one_or_none()
    if image is None:
        raise HTTPException(status_code=404, detail="Фото не найдено")
    # При заданном числе фото поштучного удаления нет: «ровно N» держится,
    # только если фото меняются все разом (владелец 02.10.2026).
    if block.required_photos:
        return JSONResponse(
            {"ok": False, "error": "Чтобы поменять фото, нажми «Заменить фото»."},
            status_code=409,
        )
    if count_task_block_submission_images(db, submission.id) <= 1:
        return JSONResponse({"ok": False, "error": "Нельзя удалить последнее фото. Сначала загрузи замену."}, status_code=409)
    db.delete(image)
    db.commit()
    return JSONResponse({"ok": True})


# ── POST /cabinet/tracker/tasks/{id}/blocks ──────────────────────────────────

@router.post("/tracker/tasks/{task_id}/blocks", response_class=JSONResponse)
def submit_cabinet_tracker_task_blocks(
    task_id: int,
    payload: TrackerTaskBlocksSubmit,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Сохранить ответы на блоки-вопросы.

    Проверка доступа к элементу обязательна: `block_id` из чужой задачи не
    должен пройти — сервер сверяет каждый ответ с блоками именно этой задачи,
    клиенту не доверяет (та же дисциплина, что была у мини-опроса).

    Обычные задания принимают ответы частями. Диагностика принимает все
    оставшиеся вопросы одним запросом, чтобы результат был однозначным.
    """
    task = _writable_task_or_404(db, user["user_id"], task_id)
    task_done = _is_task_done(db, task_id, user["user_id"])
    # Ответ на блок чужого тарифа не принимается: клиент его не получал, а
    # присланный руками `block_id` уйдёт в «Ответ на чужой вопрос» ниже.
    all_blocks = visible_blocks_for_student(
        db, get_task_blocks(db, task_id),
        viewer=BlockViewer(db, user_id=user["user_id"], tariff=user.get("tariff")),
    )
    questions = [
        b for b in task_question_blocks(all_blocks)
        if task_done or not b.hidden_until_done
    ]
    if not questions:
        raise HTTPException(status_code=404, detail="У задачи нет вопросов")
    # Автоматически оцениваемые вопросы и подтверждение правил по-прежнему
    # имеют одну попытку. Текст и шкала допускают правку до срока и проверки.
    response = get_task_block_response(db, task_id=task_id, user_id=user["user_id"])
    already = task_block_answered_ids(db, response_id=response.id) if response else set()
    visible = questions
    known = {block.id for block in visible}
    unknown = [a.block_id for a in payload.answers if a.block_id not in known]
    if unknown:
        raise HTTPException(status_code=422, detail="Ответ на чужой вопрос")
    if not payload.answers:
        raise HTTPException(status_code=422, detail="Нет ответов для сохранения")
    diagnostic_ids = {b.id for b in visible if b.is_diagnostic}
    answer_ids = {answer.block_id for answer in payload.answers}
    if diagnostic_ids and answer_ids & diagnostic_ids:
        # Диагностика — не завязана на `task.kind` (владелец 24.09.2026, она
        # может лежать в одном задании с обычными вопросами): раз в
        # запросе есть хоть один ответ на диагностику, весь запрос должен
        # быть только про неё, и целиком — иначе комбинация ответов
        # получится неоднозначной (правило от 31.08.2026, тут не менялось).
        if answer_ids - diagnostic_ids:
            raise HTTPException(
                status_code=422,
                detail="Ответы на диагностику и остальные вопросы нужно отправлять отдельно",
            )
        expected_count = len(task.diagnostic_config["questions"]) if task.diagnostic_config else 3
        if len(diagnostic_ids) != expected_count or answer_ids != diagnostic_ids - already:
            raise HTTPException(status_code=422, detail="Выбери по одному варианту в каждом вопросе")
        if len(payload.answers) != len(answer_ids):
            raise HTTPException(status_code=422, detail="Один ответ на каждый вопрос")
        for answer in payload.answers:
            if len(answer.option_ids) != 1 or answer.text:
                raise HTTPException(status_code=422, detail="Выбери один вариант в каждом вопросе")
    poll_error = poll_submission_error(
        visible,
        answered_ids=already,
        answers={
            a.block_id: {"text": a.text, "option_ids": a.option_ids, "option_texts": a.option_texts}
            for a in payload.answers
        },
    )
    if poll_error:
        raise HTTPException(status_code=422, detail=poll_error)
    # Срок — с тарифом ученика и сроком задания, как в ленте: без них
    # `deadline_reason` смотрел бы только на общий срок блока, и ученика с
    # продлённым сроком не пустили бы, а с укороченным — пустили после срока.
    # Сроки читаются одним запросом на все вопросы задания, не по блоку.
    submit_deadlines = get_task_block_submit_deadlines(db, [b.id for b in visible])
    task_submit_deadlines = get_task_level_submit_deadlines(db, [task.id]).get(task.id)
    for answer in payload.answers:
        block = next(b for b in visible if b.id == answer.block_id)
        # Первый ответ после срока принимается и пишется опозданием, правка
        # уже данного — нет (владелец 06.10.2026).
        reason = deadline_reason(
            task, block,
            user_tariff=user.get("tariff"),
            tariff_deadlines=submit_deadlines.get(block.id),
            task_tariff_deadlines=task_submit_deadlines,
            late_allowed=block.id not in already,
        )
        if reason:
            raise HTTPException(status_code=409, detail=reason)
        if block.id in already:
            if block.block_type == BLOCK_RULES or (
                block.block_type == BLOCK_QUESTION and block.question_type != QUESTION_TEXT
            ):
                raise HTTPException(status_code=409, detail="На этот вопрос уже есть ответ")
            reviewed = db.query(TaskBlockAnswer.id).filter(
                TaskBlockAnswer.response_id == response.id,
                TaskBlockAnswer.block_id == block.id,
                TaskBlockAnswer.reviewed_at.isnot(None),
            ).first()
            if reviewed:
                raise HTTPException(status_code=409, detail="Преподаватель уже проверил ответ")
    selected = {answer.block_id for answer in payload.answers}
    response = save_task_block_response(
        db,
        task_id=task_id,
        user_id=user["user_id"],
        blocks=[block for block in visible if block.id in selected],
        answers={
            a.block_id: {
                "text": a.text, "option_ids": a.option_ids,
                "option_texts": a.option_texts,
            }
            for a in payload.answers
        },
    )
    db.flush()
    # Вердикт — по всем видимым вопросам, а не только по этой порции: ученик,
    # дославший пропущенный ответ, должен видеть счёт целиком.
    verdict = grade_task_blocks(db, blocks=visible, response_id=response.id)
    db.commit()
    # Результат сразу в ответе (владелец 31.08.2026): ученик видит, где прав,
    # не перезагружая страницу.
    return JSONResponse({"ok": True, **verdict})
