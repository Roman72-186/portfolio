"""Цикл Пробника (план spec/plan_14_05_2026.md + редизайн 2026-05-23).

Цикл = 1 финальная работа Пробника + диалог обратной связи + повторные попытки.
Создаётся при загрузке финальной фото Пробника.
Закрывается вручную куратором/главным преподавателем/SA после обратной связи
(см. close_cycle) —
балл проставляется раньше и закрытие цикла его не требует автоматически.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session as DBSession

from app.models.exam_assignment import (
    ExamAssignment,
    ExamTicket,
    ExamTicketAssignee,
    ExamTicketTag,
    ExamTicketTariff,
)
from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage, FeedbackPhoto
from app.models.mock_exam_attempt import MockExamAttempt
from app.models.mock_exam_lock import MockExamLock
from app.models.tracker import ITEM_MOCK_EXAM, SOURCE_EXAM_ASSIGNMENT, TrackerTask
from app.models.user import User
from app.models.work import Work, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.mock_exam_access import (
    get_matching_target_tag_ids_for_student,
    is_mock_exam_ticket_submission_open,
    is_subject_allowed_for_student,
)
from app.services.tracker import close_task_for_user
from app.services.tz import today_msk

MAX_INTERMEDIATE_PER_FINAL = 10


def _stored_work_file_filter():
    """A Work row counts for upload quotas only when it points to a saved file."""
    return or_(
        Work.s3_path.isnot(None),
        Work.s3_url.isnot(None),
        Work.drive_file_id.isnot(None),
    )


def get_active_tickets(db: DBSession, user_id: int, subject: str) -> list[ExamTicket]:
    """Единый резолвер активных билетов текущего задания по предмету.

    Если в текущем опубликованном задании несколько билетов, возвращает их все:
    старт пробника выберет один случайно. Если есть пересекающиеся старые задания,
    берём только самое свежее подходящее задание, чтобы старый билет не открывал
    лишнюю попытку после выдачи нового.
    """
    if not is_subject_allowed_for_student(db, user_id, subject):
        return []

    assignee_ticket_ids = (
        db.query(ExamTicketAssignee.ticket_id)
        .filter(ExamTicketAssignee.user_id == user_id)
        .scalar_subquery()
    )
    matching_target_tag_ids = get_matching_target_tag_ids_for_student(db, user_id)
    # Билеты учебной программы адресуются сразу нескольким тегам: тариф плюс
    # дополнительные. Первый тег дублируется в target_tag_id ради планировщика
    # уведомлений, остальные живут только здесь. У билетов старой формы таблица
    # пустая, и подзапрос ничего не добавляет.
    tag_ticket_ids = (
        db.query(ExamTicketTag.ticket_id)
        .filter(ExamTicketTag.tag_id.in_(matching_target_tag_ids))
        .scalar_subquery()
    )
    # Тарифная видимость (созвон 26.08.2026) — тот же принцип, что у
    # accessible_topic_ids: не только видеомодульная тема элемента прячется от
    # ученика, но и сам билет становится нерезолвируемым, иначе «скрыт
    # полностью» превратилось бы в «скрыт с вкладки, но доступен по прямой
    # ссылке» (см. TrackerTask.kind == ITEM_MOCK_EXAM, отдельный механизм).
    student_tariff = (
        db.query(User.tariff).filter(User.id == user_id).scalar() or ""
    ).strip().upper()
    tariff_ok_ticket_ids = (
        db.query(ExamTicketTariff.ticket_id)
        .filter(ExamTicketTariff.tariff == student_tariff)
        .scalar_subquery()
    )
    tickets = (
        db.query(ExamTicket)
        .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
        .filter(
            ExamAssignment.status == "published",
            ExamAssignment.kind != "guest",
            ExamAssignment.subject == subject,
            or_(
                ExamTicket.target_tag_id.in_(matching_target_tag_ids),
                ExamTicket.id.in_(tag_ticket_ids),
                and_(
                    ExamTicket.target_tag_id.is_(None),
                    or_(
                        ExamTicket.assign_to_all.is_(True),
                        ExamTicket.id.in_(assignee_ticket_ids),
                    ),
                ),
            ),
            or_(
                ExamTicket.tariff_restricted.is_(False),
                ExamTicket.id.in_(tariff_ok_ticket_ids),
            ),
        )
        .order_by(
            ExamTicket.assignment_id.desc(),
            ExamTicket.start_date.desc(),
            ExamTicket.id.desc(),
        )
        .all()
    )
    active: list[ExamTicket] = []
    for ticket in tickets:
        if is_mock_exam_ticket_submission_open(ticket):
            active.append(ticket)
    if not active:
        return []

    current_assignment_id = active[0].assignment_id
    return [
        ticket
        for ticket in active
        if ticket.assignment_id == current_assignment_id
    ]


def get_active_ticket(db: DBSession, user_id: int, subject: str) -> ExamTicket | None:
    """Совместимый одиночный резолвер: первый билет текущего активного задания."""
    tickets = get_active_tickets(db, user_id, subject)
    return tickets[0] if tickets else None


def has_cycle_for_ticket(db: DBSession, user_id: int, subject: str, ticket_id: int) -> bool:
    """True если по этому билету уже есть цикл Пробника (открытый ИЛИ закрытый).

    Source of truth для правила «одна сдача на билет». Используется для истории
    конкретного варианта; доступ к следующей сдаче открывает новый пробник
    (ExamAssignment), а не другой билет внутри текущего задания.
    """
    return (
        db.query(ExamCycle.id)
        .filter(
            ExamCycle.user_id == user_id,
            ExamCycle.subject == subject,
            ExamCycle.ticket_id == ticket_id,
        )
        .first()
        is not None
    )


def has_submitted_for_ticket(db: DBSession, user_id: int, subject: str, ticket_id: int) -> bool:
    """True если по этому билету уже загружено финальное фото Пробника.

    В отличие от has_cycle_for_ticket проверяет наличие Work.is_final=True,
    а не просто факт существования цикла. Нужно, потому что цикл теперь
    создаётся при загрузке этапных фото — раньше, чем финальное.

    Финал с needs_revision=True (отправлен «на доработку») не считается сдачей —
    это позволяет _overwrite_final перезаписать его новым фото.
    """
    return (
        db.query(ExamCycle.id)
        .join(Work, Work.cycle_id == ExamCycle.id)
        .filter(
            ExamCycle.user_id == user_id,
            ExamCycle.subject == subject,
            ExamCycle.ticket_id == ticket_id,
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.is_final == True,  # noqa: E712
            Work.status == "success",
            Work.needs_revision == False,  # noqa: E712
        )
        .first()
        is not None
    )


def has_submitted_for_assignment(
    db: DBSession, user_id: int, subject: str, assignment_id: int
) -> bool:
    """True, если ученик сдал финал по любому билету текущего пробника.

    Билеты внутри одного ExamAssignment — варианты одного пробника, а не
    независимые попытки. Поэтому успешная финальная работа по одному варианту
    закрывает выдачу остальных билетов этого задания.
    """
    return (
        db.query(ExamCycle.id)
        .join(ExamTicket, ExamCycle.ticket_id == ExamTicket.id)
        .join(Work, Work.cycle_id == ExamCycle.id)
        .filter(
            ExamCycle.user_id == user_id,
            ExamCycle.subject == subject,
            ExamTicket.assignment_id == assignment_id,
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.is_final == True,  # noqa: E712
            Work.status == "success",
            Work.needs_revision == False,  # noqa: E712
        )
        .first()
        is not None
    )


def get_unsubmitted_active_tickets(db: DBSession, user_id: int, subject: str) -> list[ExamTicket]:
    """Активные билеты пробника, доступные до первой успешной финальной сдачи.

    Все возвращённые билеты принадлежат одному текущему ExamAssignment. После
    финала по любому из них доступ закрывается для всего задания; новый
    опубликованный ExamAssignment снова откроет пробник.
    """
    tickets = get_active_tickets(db, user_id, subject)
    if not tickets:
        return []
    if has_submitted_for_assignment(db, user_id, subject, tickets[0].assignment_id):
        return []
    return tickets


def has_closed_cycle_for_ticket(db: DBSession, user_id: int, subject: str, ticket_id: int) -> bool:
    """True если по этому билету уже есть ЗАКРЫТЫЙ цикл Пробника (closed_at IS NOT NULL).

    Блокировку повторной сдачи даёт has_submitted_for_ticket (как только сдан финал).
    Этот предикат используется только чтобы РАЗЛИЧИТЬ причину блокировки: закрытый
    цикл (оценён, ждём следующий пробник) vs открытый (финал сдан, ждём ОС) — влияет на
    текст 409 и подсказку кнопки предмета (см. upload_probnik_final, _locked_mock_subjects).
    """
    return (
        db.query(ExamCycle.id)
        .filter(
            ExamCycle.user_id == user_id,
            ExamCycle.subject == subject,
            ExamCycle.ticket_id == ticket_id,
            ExamCycle.closed_at.isnot(None),
        )
        .first()
        is not None
    )


def find_latest_cycle(db: DBSession, user_id: int, subject: str) -> ExamCycle | None:
    """Последний цикл пользователя по предмету (DESC по started_at, id)."""
    return (
        db.query(ExamCycle)
        .filter(ExamCycle.user_id == user_id, ExamCycle.subject == subject)
        .order_by(ExamCycle.started_at.desc(), ExamCycle.id.desc())
        .first()
    )


def find_open_cycle_for_ticket(
    db: DBSession,
    *,
    user_id: int,
    subject: str,
    ticket_id: int | None,
) -> ExamCycle | None:
    """Open cycle for the current ticket, if stage photos already created it."""
    return (
        db.query(ExamCycle)
        .filter(
            ExamCycle.user_id == user_id,
            ExamCycle.subject == subject,
            ExamCycle.ticket_id == ticket_id,
            ExamCycle.closed_at.is_(None),
        )
        .order_by(ExamCycle.started_at.desc(), ExamCycle.id.desc())
        .first()
    )


def count_cycle_intermediates(
    db: DBSession,
    *,
    cycle_id: int,
    work_type: str = WORK_TYPE_MOCK_EXAM,
) -> int:
    """Number of successfully saved non-final photos in a cycle."""
    return (
        db.query(Work)
        .filter(
            Work.cycle_id == cycle_id,
            Work.work_type == work_type,
            Work.is_final == False,  # noqa: E712
            Work.status == "success",
            _stored_work_file_filter(),
        )
        .count()
    )


def intermediate_upload_state(existing: int) -> dict[str, int]:
    """UI/API contract for the stage-photo quota."""
    remaining = max(MAX_INTERMEDIATE_PER_FINAL - existing, 0)
    return {
        "existing": existing,
        "remaining": remaining,
        "limit": MAX_INTERMEDIATE_PER_FINAL,
    }


def cycle_submission_state(
    db: DBSession,
    *,
    cycle_id: int,
    work_type: str = WORK_TYPE_MOCK_EXAM,
) -> dict[str, int | bool | None]:
    """Post-upload source of truth for the student's submitted cycle state."""
    final = (
        db.query(Work)
        .filter(
            Work.cycle_id == cycle_id,
            Work.work_type == work_type,
            Work.is_final == True,  # noqa: E712
            Work.status == "success",
            _stored_work_file_filter(),
        )
        .order_by(Work.id.desc())
        .first()
    )
    existing = count_cycle_intermediates(db, cycle_id=cycle_id, work_type=work_type)
    return {
        "verified": final is not None,
        "final_work_id": final.id if final else None,
        **intermediate_upload_state(existing),
    }


def get_or_create_cycle_for_probnik(
    db: DBSession,
    *,
    user_id: int,
    subject: str,
    ticket_id: int | None,
) -> tuple[ExamCycle, bool]:
    """Цикл для финальной Пробника.

    Логика: если последний цикл этого пользователя+предмета привязан к тому же
    билету — повторная попытка по тому же билету → возвращаем существующий.
    Иначе создаём новый цикл.

    Returns (cycle, created).
    """
    latest = find_latest_cycle(db, user_id, subject)
    # Переиспользуем только ОТКРЫТЫЙ цикл (closed_at IS NULL). Закрытый цикл —
    # завершённая попытка с обратной связью: новая загрузка должна стартовать
    # новый цикл, а не доклеивать финалку к закрытому.
    if latest is not None and latest.closed_at is None:
        if ticket_id is not None and latest.ticket_id == ticket_id:
            return latest, False
        if ticket_id is None and latest.ticket_id is None:
            return latest, False
    cycle = ExamCycle(
        user_id=user_id,
        subject=subject,
        ticket_id=ticket_id,
        started_at=today_msk(),
    )
    db.add(cycle)
    db.flush()
    return cycle, True


def next_attempt_number(db: DBSession, *, cycle_id: int, work_type: str) -> int:
    """Следующий attempt_number в рамках цикла + типа работы.

    Считаем только финальные (is_final=true) этого work_type в цикле.
    Per-type — mock_exam и retake нумеруются раздельно.
    """
    count = (
        db.query(Work)
        .filter(
            Work.cycle_id == cycle_id,
            Work.work_type == work_type,
            Work.is_final == True,  # noqa: E712
        )
        .count()
    )
    return count + 1


def get_required_cycle_for_retake(
    db: DBSession, user_id: int, subject: str
) -> ExamCycle | None:
    """Цикл для Отработки. None → клиент должен показать «Сначала пройди Пробник»."""
    return find_latest_cycle(db, user_id, subject)


def has_open_cycles(db: DBSession, user_id: int) -> bool:
    """True если у пользователя есть хотя бы один незакрытый цикл."""
    return db.query(
        db.query(ExamCycle)
        .filter(ExamCycle.user_id == user_id, ExamCycle.closed_at.is_(None))
        .exists()
    ).scalar()


def delete_open_cycle(db: DBSession, cycle: ExamCycle) -> list[str]:
    """Полностью удалить ОТКРЫТЫЙ цикл Пробника и все связанные данные.

    Сценарий: ученик ошибся при отправке, суперадмин удаляет цикл. Удаляются
    works (финалы + этапы), диалог обратной связи (feedbacks + messages + legacy
    photos), снимается блокировка пробника по предмету (иначе ученик не сможет
    пересдать) и сам цикл. Возвращает S3-пути для последующей best-effort очистки.

    Каскад выполняется ЯВНО и в порядке зависимостей: тестовый SQLite не включает
    PRAGMA foreign_keys, а в Postgres `feedbacks.work_id` и `works.parent_work_id`
    — обычные FK без ON DELETE CASCADE. Полагаться на БД нельзя; кроме того, per-row
    `db.delete()` мог бы удалить родительскую работу раньше дочерней (retake →
    parent_work_id) и упасть на FK в проде. Поэтому bulk-delete по уровням.
    """
    work_ids = [row[0] for row in db.query(Work.id).filter(Work.cycle_id == cycle.id).all()]
    s3_paths: list[str] = []
    feedback_ids: list[int] = []
    if work_ids:
        s3_paths += [
            p for (p,) in db.query(Work.s3_path)
            .filter(Work.cycle_id == cycle.id, Work.s3_path.isnot(None)).all()
        ]
        feedback_ids = [
            row[0] for row in db.query(Feedback.id).filter(Feedback.work_id.in_(work_ids)).all()
        ]
    if feedback_ids:
        s3_paths += [
            p for (p,) in db.query(FeedbackMessage.photo_s3_path)
            .filter(
                FeedbackMessage.feedback_id.in_(feedback_ids),
                FeedbackMessage.photo_s3_path.isnot(None),
            ).all()
        ]
        s3_paths += [
            p for (p,) in db.query(FeedbackMessage.video_s3_path)
            .filter(
                FeedbackMessage.feedback_id.in_(feedback_ids),
                FeedbackMessage.video_s3_path.isnot(None),
            ).all()
        ]
        s3_paths += [
            p for (p,) in db.query(FeedbackPhoto.s3_path)
            .filter(FeedbackPhoto.feedback_id.in_(feedback_ids)).all()
        ]
        db.query(FeedbackMessage).filter(
            FeedbackMessage.feedback_id.in_(feedback_ids)
        ).delete(synchronize_session=False)
        db.query(FeedbackPhoto).filter(
            FeedbackPhoto.feedback_id.in_(feedback_ids)
        ).delete(synchronize_session=False)
        db.query(Feedback).filter(
            Feedback.id.in_(feedback_ids)
        ).delete(synchronize_session=False)
    if work_ids:
        db.query(Work).filter(Work.cycle_id == cycle.id).delete(synchronize_session=False)
    # Снять блокировку пробника по предмету — как в close_cycle.
    lock = (
        db.query(MockExamLock)
        .filter(
            MockExamLock.user_id == cycle.user_id,
            MockExamLock.subject == cycle.subject,
            MockExamLock.is_locked == True,  # noqa: E712
        )
        .first()
    )
    if lock:
        lock.is_locked = False
        lock.unlocked_at = datetime.now(timezone.utc)
    db.query(ExamCycle).filter(ExamCycle.id == cycle.id).delete(synchronize_session=False)
    db.flush()
    return s3_paths


def _close_related_tracker_task(db: DBSession, cycle: ExamCycle, *, source: str) -> None:
    """Закрыть `TrackerTask(kind=mock_exam)`, которую сдаёт этот цикл.

    Косметика «Личного трекера» — месячный гейт (`is_month_complete`) проверяет
    `ExamCycle.closed_at` напрямую, не эту задачу: Пробник вне восьми вкладок
    недели (решение владельца 22.08). Резолвинг через
    `ExamCycle.ticket_id → ExamTicket.assignment_id → TrackerTask.source_id`
    (`source_kind=SOURCE_EXAM_ASSIGNMENT`) — так же, как задача создаётся в
    `cabinet_program.py`. `ticket_id is None` бывает у части циклов (легаси/
    гостевой режим) — тогда резолвить нечего, тихо выходим.
    """
    if cycle.ticket_id is None:
        return
    assignment_id = (
        db.query(ExamTicket.assignment_id)
        .filter(ExamTicket.id == cycle.ticket_id)
        .scalar()
    )
    if assignment_id is None:
        return
    task = (
        db.query(TrackerTask)
        .filter(
            TrackerTask.kind == ITEM_MOCK_EXAM,
            TrackerTask.source_kind == SOURCE_EXAM_ASSIGNMENT,
            TrackerTask.source_id == assignment_id,
            TrackerTask.deleted_at.is_(None),
        )
        .first()
    )
    if task is not None:
        close_task_for_user(db, task, cycle.user_id, source=source)


def close_cycle(db: DBSession, cycle: ExamCycle) -> bool:
    """Закрыть цикл вручную (куратор/главный преподаватель/SA после обратной связи).

    Требует, чтобы финальная попытка Пробника в цикле уже имела выставленный
    балл — балл ставится раньше, ОС даётся после, закрытие — последний шаг.
    Идемпотентна: если цикл уже закрыт — ничего не делает.

    Returns True если цикл был закрыт этим вызовом.
    """
    if cycle.closed_at is not None:
        return False
    final = (
        db.query(Work)
        .filter(
            Work.cycle_id == cycle.id,
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.is_final == True,  # noqa: E712
            Work.status == "success",
            Work.needs_revision == False,  # noqa: E712
            _stored_work_file_filter(),
        )
        .order_by(Work.attempt_number.desc(), Work.id.desc())
        .first()
    )
    if final is None or final.score is None:
        return False
    now = datetime.now(timezone.utc)
    cycle.closed_at = now
    # Закрытие цикла = пробник по предмету считается закрытым → снимаем блокировку,
    # чтобы ученик мог загрузить новый пробник, а админ-UI видел актуальный статус.
    lock = (
        db.query(MockExamLock)
        .filter(
            MockExamLock.user_id == cycle.user_id,
            MockExamLock.subject == cycle.subject,
            MockExamLock.is_locked == True,  # noqa: E712
        )
        .first()
    )
    if lock:
        lock.is_locked = False
        lock.unlocked_at = now
    _close_related_tracker_task(db, cycle, source="staff")
    db.flush()
    return True


def close_cycle_auto(db: DBSession, cycle: ExamCycle) -> bool:
    """Закрыть цикл автоматически по факту сдачи (тариф «Я С ВАМИ», без ОС).

    Зеркало `close_cycle`, но **без требования выставленного балла**: у тарифа
    без обратной связи некому его ставить и некому нажать «Закрыть цикл» — без
    этой отвязки месячный гейт для таких учеников не открылся бы никогда.
    Осознанное отступление, `closed_at` и `score` уже ортогональны в системе
    (см. докстроку `reopen_cycle` — «портфолио гейтится баллом, не закрытием»),
    балл может быть выставлен позже.

    Идемпотентна: если цикл уже закрыт — ничего не делает.
    Returns True если цикл был закрыт этим вызовом.
    """
    if cycle.closed_at is not None:
        return False
    final = (
        db.query(Work)
        .filter(
            Work.cycle_id == cycle.id,
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.is_final == True,  # noqa: E712
            Work.status == "success",
            Work.needs_revision == False,  # noqa: E712
            _stored_work_file_filter(),
        )
        .order_by(Work.attempt_number.desc(), Work.id.desc())
        .first()
    )
    if final is None:
        return False
    now = datetime.now(timezone.utc)
    cycle.closed_at = now
    lock = (
        db.query(MockExamLock)
        .filter(
            MockExamLock.user_id == cycle.user_id,
            MockExamLock.subject == cycle.subject,
            MockExamLock.is_locked == True,  # noqa: E712
        )
        .first()
    )
    if lock:
        lock.is_locked = False
        lock.unlocked_at = now
    _close_related_tracker_task(db, cycle, source="auto")
    db.flush()
    return True


def reopen_cycle(db: DBSession, cycle: ExamCycle) -> bool:
    """Переоткрыть закрытый цикл (SA, если закрыли по ошибке).

    Точное зеркало close_cycle: сбрасываем closed_at и возвращаем блокировку
    предмета (close снимал её). Балл финалки не трогаем — появление работы в
    Портфолио привязано к score, а не к closed_at (см. cabinet_student
    _mock_exam_finals: closed_only фильтрует по Work.score), поэтому переоткрытие
    не выдёргивает работу из Портфолио.

    Блокировку только восстанавливаем на существующей строке (не создаём новую) —
    у оценённого финала строка lock всегда уже есть (см. upload final flow).
    Идемпотентна: если цикл уже открыт — ничего не делает.

    Returns True если цикл был переоткрыт этим вызовом.
    """
    if cycle.closed_at is None:
        return False
    now = datetime.now(timezone.utc)
    cycle.closed_at = None
    # Переоткрытие отменяет любой висящий возврат на правку: иначе флаг держал бы
    # уже открытый цикл в списке как «на изменении». Это отмена, а не завершение —
    # чистим оба поля, чтобы цикл не выглядел «правка завершена».
    cycle.revision_requested_at = None
    cycle.revision_done_at = None
    lock = (
        db.query(MockExamLock)
        .filter(
            MockExamLock.user_id == cycle.user_id,
            MockExamLock.subject == cycle.subject,
        )
        .first()
    )
    if lock:
        lock.is_locked = True
        lock.locked_at = now
        lock.unlocked_at = None
        lock.unlocked_by_id = None
    db.flush()
    return True


def request_curator_revision(db: DBSession, cycle: ExamCycle) -> int | None:
    """SA возвращает цикл (любой — открытый или закрытый) автору ОС на правку.

    Статус цикла не меняем (closed_at/балл/портфолио/блокировка не трогаются) —
    ставим только revision_requested_at (и сбрасываем revision_done_at прошлого
    раунда). Возвращает id куратора-автора последней ОС (для адресной
    видимости/доступа) или None, если в цикле нет обратной связи
    (нечего править — вызывающий отдаёт 400).
    """
    fb = (
        db.query(Feedback)
        .join(Work, Feedback.work_id == Work.id)
        .filter(Work.cycle_id == cycle.id)
        .order_by(Feedback.id.desc())
        .first()
    )
    if fb is None:
        return None
    cycle.revision_requested_at = datetime.now(timezone.utc)
    cycle.revision_done_at = None
    db.flush()
    return fb.curator_id


def finish_curator_revision(db: DBSession, cycle: ExamCycle) -> bool:
    """Куратор завершил правку — ставим revision_done_at. Цикл уже закрыт.

    revision_requested_at сохраняется: пара requested/done даёт метрику
    «время реакции куратора на возврат». Состояние «на правке» — is_on_revision.
    Returns True если правка была завершена этим вызовом.
    """
    if not cycle.is_on_revision:
        return False
    cycle.revision_done_at = datetime.now(timezone.utc)
    db.flush()
    return True


def close_or_expire_mock_exam_attempts(
    db: DBSession, user_id: int, subject: str, ticket_id: int, now: datetime | None = None
) -> None:
    """После успешной сдачи Пробника закрывает снимки MockExamAttempt этого предмета.

    Открытая попытка ТЕКУЩЕГО билета (ticket_id) — это и есть данная сдача,
    помечается completed_at. Открытые попытки ДРУГИХ (старых/архивных) билетов
    того же предмета — устаревшие снимки от mock_exam_start, не относящиеся к
    этой сдаче; помечаются expired_at, а не completed_at, чтобы не выглядело
    так, будто они «сданы» вместе с текущей (см. _is_ticket_still_active в
    upload.py и exam_scheduler._run_mock_exam_expiry_check).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    db.query(MockExamAttempt).filter(
        MockExamAttempt.user_id == user_id,
        MockExamAttempt.subject == subject,
        MockExamAttempt.completed_at.is_(None),
        MockExamAttempt.expired_at.is_(None),
        MockExamAttempt.ticket_id == ticket_id,
    ).update({"completed_at": now}, synchronize_session=False)
    db.query(MockExamAttempt).filter(
        MockExamAttempt.user_id == user_id,
        MockExamAttempt.subject == subject,
        MockExamAttempt.completed_at.is_(None),
        MockExamAttempt.expired_at.is_(None),
        or_(
            MockExamAttempt.ticket_id != ticket_id,
            MockExamAttempt.ticket_id.is_(None),
        ),
    ).update({"expired_at": now}, synchronize_session=False)
