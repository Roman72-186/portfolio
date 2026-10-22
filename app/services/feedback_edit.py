"""Правка отправленной ОС сотрудником (владелец 05.10.2026).

**Единственное место правил «можно ли поправить сообщение ОС».** Читают роуты
правки и экраны диалогов (кнопка «Изменить»), своих копий условий у них нет —
иначе кнопка показалась бы там, где роут откажет.

Сдача в задании (`TaskBlockFeedback`):
1. Автор правит **своё** сообщение, пока ОС не завершена, как в Telegram.
2. После «Завершить ОС» правка закрыта; ГП или суперадмин может вернуть ОС
   автору диалога (`curator_id`) на правку — тогда он правит свои сообщения,
   пока не нажмёт «Завершить правку». Новые сообщения в закрытый диалог
   по-прежнему не пишутся.
3. Правится текст, вложение (фото, видео, голосовое, ссылку) можно только
   убрать — новое вложение уходит новым сообщением.

Пробник (`Feedback`): правка только после возврата цикла суперадмином
(`ExamCycle.is_on_revision`), только текст и только автором этой ОС
(`Feedback.curator_id`) — раньше её мог править любой сотрудник со своим
сообщением в цикле.

Удаление ОС по сдаче целиком (21.10.2026) — `task_block_delete_refusal`: ГП
или суперадмин, пока ОС не завершена, не оценена и ученик не ответил.

Общее для обоих: после оценки ученика правка закрыта — оценка поставлена за
конкретный текст, переписать его значило бы подменить, что оценили. Каждая
правка ставит `edited_at`, экран пишет «изменено».
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.models.audit_log import AuditLog
from app.models.feedback import Feedback, FeedbackMessage
from app.models.feedback_rating import DIALOG_MOCK_EXAM, DIALOG_TASK_BLOCK
from app.models.notification import Notification
from app.models.work import Work
from app.services.feedback import ROLE_STUDENT
from app.services.feedback_rating import _absolute, get_rating, has_staff_message, is_closed

# Вложения, которые можно убрать из сообщения: ключ формы → поля строки.
# Файл в S3 не удаляется: убранное вложение остаётся восстановимым, а
# сообщений с правкой единицы.
REMOVABLE_ATTACHMENTS: dict[str, tuple[str, ...]] = {
    "photo": ("photo_s3_path", "photo_s3_url"),
    "video": ("video_s3_path", "video_s3_url"),
    "audio": ("audio_s3_path", "audio_s3_url"),
    "video_link": ("video_url",),
}

RATED_REFUSAL = "Ученик уже оценил эту обратную связь – править её нельзя."


class EditError(Exception):
    """Отказ с HTTP-статусом и текстом для человека: роут отдаёт его как есть."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _own_staff_message(message, user_id: int) -> str | None:
    if message.sender_role == ROLE_STUDENT:
        return "Сообщения ученика не редактируются."
    if message.sender_id != user_id:
        return "Можно править только своё сообщение."
    return None


# ── Сдача в задании ──────────────────────────────────────────────────────────

def task_block_edit_refusal(db: DBSession, feedback, message, user_id: int) -> str | None:
    """Почему `user_id` не может править `message`, или None — может."""
    refusal = _own_staff_message(message, user_id)
    if refusal:
        return refusal
    if get_rating(db, DIALOG_TASK_BLOCK, feedback.id) is not None:
        return RATED_REFUSAL
    if not is_closed(feedback):
        return None
    if feedback.is_on_revision and feedback.curator_id == user_id:
        return None
    return "Обратная связь завершена. Поправить её можно, если Главный преподаватель вернёт её на правку."


def editable_task_block_message_ids(db: DBSession, feedback, user_id: int) -> set[int]:
    if feedback is None:
        return set()
    return {
        message.id for message in feedback.messages
        if task_block_edit_refusal(db, feedback, message, user_id) is None
    }


def task_block_return_refusal(db: DBSession, feedback) -> str | None:
    """Почему ОС по сдаче нельзя вернуть автору на правку, или None."""
    if not has_staff_message(feedback):
        return "В диалоге нет обратной связи – править нечего."
    if not is_closed(feedback):
        return "Обратная связь ещё не завершена – автор правит свои сообщения сам."
    if get_rating(db, DIALOG_TASK_BLOCK, feedback.id) is not None:
        return RATED_REFUSAL
    if feedback.is_on_revision:
        return "Обратная связь уже возвращена на правку."
    return None


# Удаление ошибочной ОС (владелец 21.10.2026): кураторы отправляли ОС не
# тем ученикам и не на те работы, школа трижды за два дня просила убрать её
# вручную (`docs/ops-history.md`, 20–21.10.2026). Удаляет ГП или суперадмин —
# ранг проверяет роут, как у возврата на правку.

# Уведомления ученику, которые рождает незавершённый диалог: по одному на
# каждое сообщение сотрудника до 22.10.2026, с тех пор — по одному на нажатие
# «Отправить ОС» (`task_block_feedback.notify_counterpart`).
# «Преподаватель оценил работу» — про балл, а не про диалог, и остаётся.
DELETED_FEEDBACK_NOTIFICATION_TITLES = (
    "Преподаватель оставил обратную связь по работе",
    "Преподаватель оставил обратную связь по работам",
)


def task_block_delete_refusal(db: DBSession, feedback) -> str | None:
    """Почему ОС по сдаче нельзя удалить целиком, или None — можно."""
    if not has_staff_message(feedback):
        return "В диалоге нет обратной связи – удалять нечего."
    if is_closed(feedback):
        return ("Обратная связь завершена – удалить её нельзя. "
                "Если в ней ошибка, верни её автору на правку.")
    if get_rating(db, DIALOG_TASK_BLOCK, feedback.id) is not None:
        return RATED_REFUSAL
    if any(message.sender_role == ROLE_STUDENT for message in feedback.messages):
        return "Ученик уже ответил в диалоге – удалить обратную связь нельзя."
    return None


def delete_task_block_feedback(db: DBSession, feedback, submission, *, user_id: int) -> None:
    """Удалить диалог целиком, как ручная чистка 20–21.10.2026: уведомления
    ученику о нём, сам диалог (сообщения уходят каскадом) и отметку
    «проверено» у сдачи — работа возвращается в очередь. Балл не трогаем
    (владелец 21.10.2026): его ставят отдельно от диалога. Файлы в S3 остаются,
    как при правке. Проверку `task_block_delete_refusal` делает вызывающий."""
    messages = feedback.messages
    first_text = next((m.text for m in messages if m.text), None) or ""
    db.add(AuditLog(
        action="task_block_feedback_delete",
        performed_by_id=user_id,
        target_user_id=submission.user_id,
        details=json.dumps({
            "feedback_id": feedback.id,
            "submission_id": submission.id,
            "author_id": feedback.curator_id,
            "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
            "messages": len(messages),
            "text_start": first_text[:300],
        }, ensure_ascii=False),
    ))
    db.query(Notification).filter(
        Notification.user_id == submission.user_id,
        Notification.task_block_submission_id == submission.id,
        Notification.title.in_(DELETED_FEEDBACK_NOTIFICATION_TITLES),
    ).delete(synchronize_session=False)
    db.delete(feedback)
    submission.reviewed_at = None
    submission.reviewed_by_id = None
    db.flush()
    invalidate_unread(submission.user_id)


def request_task_block_revision(feedback) -> None:
    feedback.revision_requested_at = datetime.now(timezone.utc)
    feedback.revision_done_at = None


def finish_task_block_revision(feedback) -> bool:
    """«Завершить правку». False — диалог не на правке."""
    if not feedback.is_on_revision:
        return False
    feedback.revision_done_at = datetime.now(timezone.utc)
    return True


def revision_request_notification(
    db: DBSession, *, author_id: int, subject: str, link_path: str,
    task_block_submission_id: int,
) -> Notification:
    """Автору ОС: её вернули на правку. Ссылка — в тексте, бот ссылок
    уведомления не видит (как у `rate_request_notification`)."""
    notification = Notification(
        user_id=author_id,
        title="Обратную связь вернули на правку",
        text=(
            f"Обратную связь по работе {subject} вернули на правку. "
            f"Поправь свои сообщения и нажми «Завершить правку»: {_absolute(link_path)}"
        ),
        task_block_submission_id=task_block_submission_id,
    )
    db.add(notification)
    db.flush()
    invalidate_unread(author_id)
    return notification


def edited_feedback_notification(
    db: DBSession, *, student_id: int, subject: str, link_path: str,
    task_block_submission_id: int,
) -> Notification:
    """Ученику: завершённую ОС поправили. Пока ОС открыта, правку видно
    пометкой «изменено» без уведомления — иначе каждая опечатка слала бы его."""
    notification = Notification(
        user_id=student_id,
        title="Обратная связь обновлена",
        text=f"Преподаватель обновил обратную связь по работе {subject}: {_absolute(link_path)}",
        task_block_submission_id=task_block_submission_id,
    )
    db.add(notification)
    db.flush()
    invalidate_unread(student_id)
    return notification


# ── Пробник ──────────────────────────────────────────────────────────────────

def mock_exam_edit_refusal(db: DBSession, feedback, message, cycle, user_id: int) -> str | None:
    refusal = _own_staff_message(message, user_id)
    if refusal:
        return refusal
    if cycle is None or not cycle.is_on_revision:
        return "Правка доступна только когда суперадмин вернул цикл на изменение."
    if feedback is None or feedback.curator_id != user_id:
        return "Править может только автор этой обратной связи."
    if get_rating(db, DIALOG_MOCK_EXAM, feedback.id) is not None:
        return RATED_REFUSAL
    return None


def editable_mock_exam_message_ids(db: DBSession, cycle, user_id: int) -> set[int]:
    """Сообщения цикла, под которыми экран рисует «✎ Изменить» этому сотруднику."""
    if cycle is None or not cycle.is_on_revision:
        return set()
    feedbacks = {
        feedback.id: feedback for feedback in
        db.query(Feedback).join(Work, Feedback.work_id == Work.id)
        .filter(Work.cycle_id == cycle.id, Feedback.curator_id == user_id).all()
    }
    if not feedbacks:
        return set()
    messages = db.query(FeedbackMessage).filter(
        FeedbackMessage.feedback_id.in_(feedbacks), FeedbackMessage.sender_id == user_id,
    ).all()
    return {
        message.id for message in messages
        if mock_exam_edit_refusal(db, feedbacks[message.feedback_id], message, cycle, user_id) is None
    }


def mock_exam_cycle_fully_rated(db: DBSession, cycle) -> bool:
    """Вся ОС цикла уже оценена учеником: возвращать такой цикл на правку
    бессмысленно — править там уже ничего нельзя. Цикл без ОС — False, его
    отсекает своя проверка «нечего править»."""
    feedback_ids = [
        row.id for row in db.query(Feedback.id).join(Work, Feedback.work_id == Work.id)
        .filter(Work.cycle_id == cycle.id).all()
    ]
    return bool(feedback_ids) and all(
        get_rating(db, DIALOG_MOCK_EXAM, feedback_id) is not None for feedback_id in feedback_ids
    )


# ── Сама правка ──────────────────────────────────────────────────────────────

def apply_edit(message, *, text: str, remove: set[str] = frozenset()) -> bool:
    """Записать новый текст и убрать отмеченные вложения. Пустым сообщение не
    остаётся — у таблиц стоит такая же проверка, лучше отказать словами.
    False — менять было нечего, `edited_at` не трогаем."""
    unknown = remove - REMOVABLE_ATTACHMENTS.keys()
    if unknown:
        raise EditError(422, "Неизвестное вложение.")
    text_clean = (text or "").strip() or None
    attachments_left = any(
        getattr(message, fields[-1]) for key, fields in REMOVABLE_ATTACHMENTS.items()
        if key not in remove
    )
    if text_clean is None and not attachments_left:
        raise EditError(422, "Сообщение не может остаться пустым.")
    changed = text_clean != message.text or any(
        getattr(message, REMOVABLE_ATTACHMENTS[key][-1]) for key in remove
    )
    if not changed:
        return False
    message.text = text_clean
    for key in remove:
        for field in REMOVABLE_ATTACHMENTS[key]:
            setattr(message, field, None)
        if key == "video":
            message.video_is_note = False
    message.edited_at = datetime.now(timezone.utc)
    return True


__all__ = [
    "DELETED_FEEDBACK_NOTIFICATION_TITLES", "EditError", "REMOVABLE_ATTACHMENTS", "apply_edit",
    "delete_task_block_feedback", "edited_feedback_notification",
    "editable_mock_exam_message_ids", "editable_task_block_message_ids",
    "finish_task_block_revision", "mock_exam_cycle_fully_rated", "mock_exam_edit_refusal",
    "request_task_block_revision", "revision_request_notification",
    "task_block_delete_refusal", "task_block_edit_refusal", "task_block_return_refusal",
]
