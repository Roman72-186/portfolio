"""«Личный трекер» ученика — плоский список: что горит, что в работе, что закрыто.

По макету созвона 17.08 это не календарь и не разбивка по дням — вертикальный
список задач с цветным статусом (`ITEM_KIND_LABELS`/`task_status`), как
Trello-чеклист без досок. Разбивка по дням недели и календарная полоска — это
`/cabinet/learning` («Актуальное образовательное пространство»), сюда она не
относится (см. `session-handoffs/current-program.md`, правка от 21.08).

Просроченное копится без нижней границы по времени — долг не имеет смысла
терять после смены недели, ученик должен видеть его, пока не закроет.
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
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.db.database import get_db
from app.dependencies import require_csrf_header, require_student
from app.models.learning_video import LearningVideo
from app.models.task_block import (
    BLOCK_PHOTO, BLOCK_PORTFOLIO, BLOCK_QUESTION, BLOCK_RULES, BLOCK_SCALE, BLOCK_TIMED,
    BLOCK_UPLOAD, BLOCK_VIDEO, MAX_BLOCKS, MAX_SUBMISSION_IMAGES, QUESTION_TEXT,
    SCALE_MAX, SCALE_MIN, SUBMISSION_BLOCK_TYPES, TaskBlock,
)
from app.models.tracker import (
    EVENT_KIND_LABELS,
    ITEM_HOMEWORK,
    ITEM_MOCK_EXAM,
    STATUS_DONE,
    STATUS_OPEN,
    TrackerTask,
    TrackerTaskState,
)
from app.models.work import WORK_TYPE_BEFORE, Work
from app.services.program import day_bounds, item_details, msk_date, week_start
from app.services.stats import avg_score_by_subject_all_time
from app.services import s3 as s3_service
from app.services.task_blocks import (
    add_submission_image as add_task_block_submission_image,
    answered_block_ids as task_block_answered_ids,
    close_block_for_user as close_task_block_for_user,
    count_submission_images as count_task_block_submission_images,
    get_answers_map as get_task_block_answers_map,
    get_or_create_submission as get_or_create_task_block_submission,
    get_submission as get_task_block_submission,
    list_submission_images as list_task_block_submission_images,
    mark_submitted as mark_task_block_submitted,
    get_blocks as get_task_blocks,
    get_images as get_task_block_images,
    grade_response as grade_task_blocks,
    get_options as get_task_block_options,
    get_response as get_task_block_response,
    get_selected_option_texts as get_task_block_selected_option_texts,
    get_selected_options as get_task_block_selected_options,
    get_state as get_task_block_state,
    question_blocks as task_question_blocks,
    start_timed_block as start_task_timed_block,
    timed_overrun as task_block_timed_overrun,
    save_response as save_task_block_response,
)
from app.services.tracker import (
    accessible_task_entries,
    accessible_task_ids,
    active_digest_for_student,
    active_goal_for_student,
    effective_week_start,
    list_events,
    task_status,
)
from app.services.tz import today_msk, now_msk
from app.services.upload_validation import read_image_uploads
from app.services.utils import compress_image
from app.services.video_progress import get_video_progress
from app.services.video_topics import accessible_topic_ids
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


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

    # Дайджест месяца — первый блок на экране (решение владельца 22.08).
    digest = active_digest_for_student(db, user["user_id"], year=today.year, month=today.month)
    digest_events = list_events(db, digest.id) if digest is not None else []
    goal = active_goal_for_student(db, user["user_id"], today=today)

    overdue = [e for e in entries if e["status"] == "overdue"]
    upcoming = [e for e in entries if e["status"] == "upcoming"]
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

    return templates.TemplateResponse(request, "cabinet_tracker.html", {
        "request": request,
        "user": user,
        "overdue": overdue,
        "upcoming": upcoming,
        "done": done,
        "digest": digest,
        "digest_events": digest_events,
        "event_kind_labels": EVENT_KIND_LABELS,
        "goal": goal,
        # Красное предупреждение (решение владельца 23.08, гейт «блок → неделя
        # → месяц»): ученик застрял на прошлой неделе, а не идёт по текущей.
        "is_behind_schedule": effective_week_start(db, user["user_id"], today) < week_monday,
        "active_tab": "tracker",
        "avg_score_by_subject": avg_score_by_subject_all_time(db, user["user_id"]),
        # Нужен partial'у `partials/task_action.html`: видео, пробник и домашка
        # ведут на свой экран, галочка остаётся только у остального.
        "details": item_details(db, [e["task"] for e in entries]),
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

    topic_ids = accessible_topic_ids(db, user["user_id"])
    task_ids = accessible_task_ids(db, user["user_id"])
    accessible = (task.topic_id is not None and task.topic_id in topic_ids) or (
        task.topic_id is None and task.id in task_ids
    )
    if not accessible:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    # Гейт «нельзя закрыть, пока не отвечены вопросы» (владелец 31.08.2026).
    # Считаем только **видимые сейчас** вопросы: скрытые до сдачи в проверку
    # не входят, иначе выходил бы тупик — вопрос не виден, ответить нельзя,
    # задание не закрыть, и вся неделя встала бы за ним.
    pending = [
        block for block in task_question_blocks(get_task_blocks(db, task_id))
        if not block.hidden_until_done
    ]
    if pending and get_task_block_response(
        db, task_id=task_id, user_id=user["user_id"]
    ) is None:
        raise HTTPException(
            status_code=409, detail="Сначала ответьте на вопросы задания"
        )

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
    return task


def _is_task_done(db: DBSession, task_id: int, user_id: int) -> bool:
    state = (
        db.query(TrackerTaskState)
        .filter(TrackerTaskState.task_id == task_id, TrackerTaskState.user_id == user_id)
        .one_or_none()
    )
    return state is not None and state.status == STATUS_DONE


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
    task = db.get(TrackerTask, block.task_id)
    since = block.opens_at or (task.starts_at if task else None)
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
    """Досмотрел ли ученик ролик видео-блока — по `VideoProgress.completed_at`,
    той же проверке антиперемотки, что уже стоит на видео-задаче целиком
    (`api/video.py::_save_progress`: сервер сам решает «досмотрел», клиенту не
    верим). «Просмотрено» — это только допуск к кнопке ниже, не сама отметка
    выполнения: блок закрывает ученик явным кликом (владелец 12.09.2026:
    «кружок нужно отметить, но проверяем, просмотрено видео или нет» — кто
    ставит отметку и кто её проверяет, две разные роли).

    `completed_at` — отметка на всю жизнь ролика (`video_progress.py::
    save_video_progress`: «preserve the first completion timestamp», не
    сбрасывается новым апсертом), не на конкретный блок или цикл. Если один
    и тот же каталожный ролик когда-нибудь привяжут вторым блоком к другому
    занятию — старый просмотр молча откроет кружок там без нового просмотра.
    Не чиним искусственным окном по датам блока (как `_portfolio_block_done`
    от загрузок): у видео-задачи `TrackerTask.kind == 'video'` то же
    поведение уже много месяцев (`_close_video_task_once` смотрит на тот же
    `completed_at`), и один ролик под два разных блока/задачи на практике не
    заводят — у каждого урока свой каталожный ролик.
    """
    if not block.video_id:
        return False
    video = db.get(LearningVideo, block.video_id)
    if video is None:
        return False
    progress = get_video_progress(db, user_id=user_id, video_id=video.bunny_video_id)
    return bool(progress and progress.completed_at is not None)


def _submission_payload(db: DBSession, block, user_id: int) -> dict:
    """Что ученик уже сдал в этом блоке — общая часть «загрузки работ» и
    «работы на время»: у них одна механика приёма, разная только обёртка."""
    submission = get_task_block_submission(db, block_id=block.id, user_id=user_id)
    images = (
        list_task_block_submission_images(db, submission.id) if submission else []
    )
    return {
        "upload_endpoint": f"/cabinet/tracker/blocks/{block.id}/upload",
        "max_files": MAX_SUBMISSION_IMAGES,
        "submitted_files": [i.image_s3_url for i in images],
        "submitted_comment": submission.comment if submission else None,
        "reviewed": bool(submission and submission.reviewed_at),
        "review_comment": submission.review_comment if submission else None,
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
    _accessible_task_or_404(db, user["user_id"], task_id)

    task_done = _is_task_done(db, task_id, user["user_id"])
    # Скрытый вопрос появляется только после закрытия задания — и весь блок
    # целиком, а не одна форма ответа: до сдачи ученик о нём не знает.
    blocks = [
        b for b in get_task_blocks(db, task_id)
        if not (b.block_type == BLOCK_QUESTION and b.hidden_until_done and not task_done)
    ]
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

    payload = []
    for block in blocks:
        item = {
            "id": block.id,
            "block_type": block.block_type,
            "title": block.title,
            "body": block.body,
            # Запирается отдельный вопрос, а не форма разом: на пропущенный
            # ученик должен иметь возможность вернуться.
            "answered": block.id in answered_ids,
        }
        if block.block_type == BLOCK_VIDEO:
            item["video_id"] = block.video_id
            item["video_embed_endpoint"] = (
                f"/cabinet/videos/{block.video_id}/embed" if block.video_id else None
            )
            # Кружок в углу карточки: закрыт ученик отмечает сам, но кнопка
            # принимает отметку, только когда сервер видит по `VideoProgress`,
            # что ролик действительно досмотрен (владелец 12.09.2026).
            state = get_task_block_state(db, block_id=block.id, user_id=user["user_id"])
            item["done"] = bool(state and state.status == STATUS_DONE)
            item["watched"] = _video_block_watched(db, block, user["user_id"])
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
        elif block.block_type == "link":
            item["url"] = block.url
        elif block.block_type == BLOCK_SCALE:
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
            item["overrun"] = task_block_timed_overrun(block, state)
            item["start_endpoint"] = f"/cabinet/tracker/blocks/{block.id}/start"
            item.update(_submission_payload(db, block, user["user_id"]))
        elif block.block_type == BLOCK_UPLOAD:
            # Работы грузятся здесь же, ученик никуда не уходит (владелец
            # 07.09.2026). `done` берём из состояния блока: его ставит сам
            # роут загрузки, а не пересчёт по портфолио.
            state = get_task_block_state(db, block_id=block.id, user_id=user["user_id"])
            item["done"] = bool(state and state.status == STATUS_DONE)
            item.update(_submission_payload(db, block, user["user_id"]))
        elif block.block_type == BLOCK_PORTFOLIO:
            # Ведём на существующий экран загрузки работ, своего у блока нет.
            # Всегда в раздел «До» (владелец 09.09.2026: «по этой кнопке работы
            # загружаются в ДО»). Без явного `section` экран выбирает раздел
            # сам по `portfolio_do_completed`, и ученик, уже грузивший работы,
            # попадал в «После». Раздел доезжает и до отправки: `upload.html`
            # кладёт `section` скрытым полем формы.
            item["upload_url"] = "/upload?section=before"
            item["done"] = _portfolio_block_done(db, block, user["user_id"])
        elif block.block_type == BLOCK_QUESTION:
            item["question_type"] = block.question_type
            item["options"] = [
                # `is_correct` наружу не отдаём: ученик не должен видеть
                # правильный ответ в теле ответа сервера. `requires_text`
                # нужен фронту, чтобы понять, под каким вариантом раскрывать
                # поле свободного текста (владелец 05.09.2026).
                {"id": o.id, "text": o.text, "requires_text": o.requires_text}
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
        elif block.block_type == BLOCK_RULES:
            # Правила школы: варианты — сами правила, `body` — текст согласия.
            # Отмеченные отдаём, чтобы уже закрытый блок открывался с
            # проставленными галочками, а не пустым.
            item["options"] = [
                {"id": o.id, "text": o.text}
                for o in options.get(block.id, [])
            ]
            item["answer_option_ids"] = sorted(selected.get(block.id, set()))
        payload.append(item)

    return JSONResponse({
        "blocks": payload,
        "has_questions": bool(questions),
        # Одна попытка (владелец 31.08.2026): ответил — форма закрывается.
        # Иначе, увидев «неверно», можно было бы переотправить до победы, и
        # счёт перестал бы что-либо значить.
        "answered": answered,
        "submit_endpoint": (
            f"/cabinet/tracker/tasks/{task_id}/blocks"
            if questions_left else None
        ),
        "correct_count": verdict["correct_count"] if verdict else None,
        "gradable_count": verdict["gradable_count"] if verdict else None,
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
    _accessible_task_or_404(db, user["user_id"], block.task_id)
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

    Отметку ставит ученик кликом, а не факт просмотра сам по себе — иначе
    кружок закрывался бы раньше, чем ученик успел это увидеть (владелец
    12.09.2026). Но отправка принимается, только когда сервер сам видит по
    `VideoProgress`, что ролик действительно досмотрен: без этой проверки
    кружок был бы обходом того же гейта, ради которого у видео-задачи целиком
    нет ручной кнопки «Отметить» (`partials/task_action.html`).
    """
    block = db.get(TaskBlock, block_id)
    if block is None or block.block_type != BLOCK_VIDEO:
        raise HTTPException(status_code=404, detail="Блок не найден")
    _accessible_task_or_404(db, user["user_id"], block.task_id)
    if not _video_block_watched(db, block, user["user_id"]):
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
    if block is None or block.block_type != BLOCK_PHOTO:
        raise HTTPException(status_code=404, detail="Блок не найден")
    _accessible_task_or_404(db, user["user_id"], block.task_id)
    close_task_block_for_user(db, block=block, user_id=user["user_id"], source="photo_confirmed")
    db.commit()
    return JSONResponse({"ok": True})


MAX_UPLOAD_FILE_SIZE = 10 * 1024 * 1024


@router.post("/tracker/blocks/{block_id}/upload", response_class=JSONResponse)
async def upload_task_block_work(
    block_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
    photos: list[UploadFile] = File(...),
    comment: str | None = Form(default=None),
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
    _accessible_task_or_404(db, user["user_id"], block.task_id)

    submission = get_or_create_task_block_submission(
        db, block=block, user_id=user["user_id"]
    )
    existing = count_task_block_submission_images(db, submission.id)
    if existing >= MAX_SUBMISSION_IMAGES:
        return JSONResponse(
            {
                "ok": False,
                "error": f"Лимит файлов исчерпан: уже загружено {existing} из {MAX_SUBMISSION_IMAGES}",
            },
            status_code=422,
        )
    files, err = await read_image_uploads(
        photos,
        max_files=MAX_SUBMISSION_IMAGES - existing,
        max_size=MAX_UPLOAD_FILE_SIZE,
    )
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=422)

    created = 0
    for filename, data in files:
        s3_path = s3_service.s3_path_task_block_submission(
            user["vk_id"], submission.id, filename, user.get("tariff") or "",
        )
        url = s3_service.upload_to_s3(s3_path, compress_image(data), "image/jpeg")
        if s3_service.is_configured() and not url:
            return JSONResponse(
                {"ok": False, "error": "Ошибка загрузки в хранилище"}, status_code=502
            )
        add_task_block_submission_image(
            db, submission=submission, url=url or "", path=s3_path if url else None
        )
        created += 1

    if created:
        mark_task_block_submitted(db, submission=submission, comment=comment)
        # Закрываем блок фактом сдачи: работа приехала, лента едет дальше.
        # Проверка куратора на это не влияет — иначе ученик стоял бы в ленте,
        # пока куратор не дойдёт до его работы (то же правило, что у домашки
        # на тарифе без обратной связи).
        close_task_block_for_user(
            db, block=block, user_id=user["user_id"], source="submission"
        )
    db.commit()
    return JSONResponse({"ok": True, "created": created})


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

    Ответы принимаются частями: ученик может ответить на один вопрос из трёх
    и вернуться позже, поэтому «число ответов равно числу вопросов» больше не
    требуется — сохраняется то, что прислали.
    """
    _accessible_task_or_404(db, user["user_id"], task_id)
    task_done = _is_task_done(db, task_id, user["user_id"])
    all_blocks = get_task_blocks(db, task_id)
    questions = [
        b for b in task_question_blocks(all_blocks)
        if task_done or not b.hidden_until_done
    ]
    if not questions:
        raise HTTPException(status_code=404, detail="У задачи нет вопросов")
    # Одна попытка — на вопрос, а не на задание целиком (уточнено 07.09.2026).
    # Правило 31.08 не даёт переотправлять ответ до победы, и оно остаётся:
    # уже отвеченный вопрос сюда не пройдёт. Но пропущенный ученик обязан
    # иметь возможность дослать — иначе обязательный вопрос без ответа
    # запирает ленту навсегда, а форма отправки уже исчезла.
    response = get_task_block_response(db, task_id=task_id, user_id=user["user_id"])
    already = task_block_answered_ids(db, response_id=response.id) if response else set()
    visible = questions
    questions = [block for block in visible if block.id not in already]
    if not questions:
        raise HTTPException(status_code=409, detail="Вы уже отвечали на это задание")
    known = {block.id for block in questions}
    if [a.block_id for a in payload.answers if a.block_id in already]:
        raise HTTPException(status_code=409, detail="На этот вопрос вы уже ответили")
    unknown = [a.block_id for a in payload.answers if a.block_id not in known]
    if unknown:
        raise HTTPException(status_code=422, detail="Ответ на чужой вопрос")
    response = save_task_block_response(
        db,
        task_id=task_id,
        user_id=user["user_id"],
        blocks=questions,
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
