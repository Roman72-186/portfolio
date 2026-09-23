"""Статистика прохождения диагностики АРХИ-ПРОФИЛЯ (владелец 24.09.2026).

Куратору/ГП нужно видеть: кто не начал, кто начал и бросил, кто закончил —
за сколько времени и с каким результатом, плюс распределение результатов по
всей аудитории задания и разрез по тарифам.

Аудитория считается точно по адресации самой задачи (`task_audience_user_ids`),
а не «все активные ученики» — иначе диагностика, назначенная одному тегу,
показала бы «не начал» про учеников, которым она вообще не видна.
"""
from app.constants import TARIFFS
from app.models.task_block import TaskBlockResponse
from app.models.tracker import TrackerTask, TrackerTaskState
from app.models.user import User
from app.services.archi_profile import result_for_answers
from app.services.tracker import task_audience_user_ids
from app.services.tz import msk_text
from sqlalchemy.orm import Session

STATUS_NOT_STARTED = "not_started"
STATUS_IN_PROGRESS = "in_progress"
STATUS_FINISHED = "finished"

STATUS_LABELS = {
    STATUS_NOT_STARTED: "Не начал",
    STATUS_IN_PROGRESS: "Начал, не закончил",
    STATUS_FINISHED: "Закончил",
}


def _duration_text(seconds: float | None) -> str | None:
    """Секунды → «3 мин 12 с» человеку. `None`, если не с чем сравнивать
    (момент начала не зафиксирован — диагностика пройдена до 24.09.2026,
    когда `started_at` появился, или отправлена в обход мастера)."""
    if seconds is None:
        return None
    total = max(0, round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes == 0:
        return f"{secs} с"
    return f"{minutes} мин {secs} с"


def diagnostic_stats(db: Session, task: TrackerTask) -> dict:
    """Прохождение одной диагностики по всей её аудитории.

    Один запрос на `TrackerTaskState` и один на `TaskBlockResponse` по всей
    аудитории разом — не по ученику в цикле, чтобы не разъезжаться по числу
    запросов с размером группы.
    """
    audience_ids = task_audience_user_ids(db, task.id)
    students = (
        db.query(User).filter(User.id.in_(audience_ids)).all()
        if audience_ids else []
    )
    students.sort(key=lambda u: (u.name or u.tg_username or str(u.id)))

    states_by_user = {
        state.user_id: state
        for state in db.query(TrackerTaskState).filter(
            TrackerTaskState.task_id == task.id,
            TrackerTaskState.user_id.in_(audience_ids),
        ).all()
    } if audience_ids else {}
    responses_by_user = {
        response.user_id: response
        for response in db.query(TaskBlockResponse).filter(
            TaskBlockResponse.task_id == task.id,
            TaskBlockResponse.user_id.in_(audience_ids),
        ).all()
    } if audience_ids else {}

    rows = []
    status_counts = {STATUS_NOT_STARTED: 0, STATUS_IN_PROGRESS: 0, STATUS_FINISHED: 0}
    profile_counts: dict[str, int] = {}
    for student in students:
        state = states_by_user.get(student.id)
        response = responses_by_user.get(student.id)
        started_at = state.started_at if state else None
        profile = None
        finished_at = None
        duration_seconds = None
        if response is not None:
            profile = result_for_answers(db, task.id, student.id)
            if profile is not None:
                status = STATUS_FINISHED
                finished_at = response.created_at
                if started_at is not None:
                    duration_seconds = (finished_at - started_at).total_seconds()
                profile_counts[profile["title"]] = profile_counts.get(profile["title"], 0) + 1
            else:
                # Ответ сохранён, но не на все обязательные вопросы — тот же
                # признак «в процессе», что видит сам ученик (`questions_left`
                # в `cabinet_tracker.py`).
                status = STATUS_IN_PROGRESS
        elif started_at is not None:
            status = STATUS_IN_PROGRESS
        else:
            status = STATUS_NOT_STARTED
        status_counts[status] += 1
        rows.append({
            "student": student,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "started_at_text": msk_text(started_at),
            "finished_at_text": msk_text(finished_at),
            "duration_text": _duration_text(duration_seconds),
            "profile": profile,
        })

    by_tariff: dict[str, dict[str, int]] = {
        tariff: {STATUS_NOT_STARTED: 0, STATUS_IN_PROGRESS: 0, STATUS_FINISHED: 0}
        for tariff in TARIFFS
    }
    for row in rows:
        tariff = (row["student"].tariff or "").strip().upper()
        if tariff in by_tariff:
            by_tariff[tariff][row["status"]] += 1

    return {
        "task": task,
        "total": len(rows),
        "rows": rows,
        "status_counts": status_counts,
        "status_labels": STATUS_LABELS,
        "profile_counts": sorted(profile_counts.items(), key=lambda item: -item[1]),
        "tariffs": TARIFFS,
        "by_tariff": by_tariff,
    }
