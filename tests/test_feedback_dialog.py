"""Тесты редизайна 2026-05-23: диалог обратной связи + закрытие цикла."""
from datetime import date, datetime, timezone

import pytest

from unittest.mock import patch

from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage
from app.models.mock_exam_lock import MockExamLock
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services import feedback as feedback_service
from app.services.exam_cycle import close_cycle, has_open_cycles, reopen_cycle


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mk_cycle(db, user_id, subject="Drawing", closed=False):
    c = ExamCycle(
        user_id=user_id, subject=subject,
        started_at=date(2026, 5, 10),
        closed_at=datetime.now(timezone.utc) if closed else None,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _mk_final_work(db, user_id, cycle_id, *, score=None, attempt=1, needs_revision=False):
    w = Work(
        user_id=user_id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="05", year=2026,
        filename=f"final-{attempt}.jpg",
        subject="Drawing",
        status="success",
        s3_url=f"https://example.test/final-{attempt}.jpg",
        is_final=True,
        cycle_id=cycle_id,
        attempt_number=attempt,
        score=score,
        needs_revision=needs_revision,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


# ── Service layer: has_open_cycles ───────────────────────────────────────────

def test_has_open_cycles_false_without_cycles(db, regular_user):
    assert has_open_cycles(db, regular_user.id) is False


def test_has_open_cycles_true_with_open_cycle(db, regular_user):
    _mk_cycle(db, regular_user.id, closed=False)
    assert has_open_cycles(db, regular_user.id) is True


def test_has_open_cycles_false_when_all_closed(db, regular_user):
    _mk_cycle(db, regular_user.id, closed=True)
    assert has_open_cycles(db, regular_user.id) is False


# ── Service layer: close_cycle ────────────────────────────────────────────────

def test_close_cycle_scored_final_mock_closes_cycle(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id)
    _mk_final_work(db, regular_user.id, cycle.id, score=82)
    assert close_cycle(db, cycle) is True
    db.refresh(cycle)
    assert cycle.closed_at is not None


def test_close_cycle_unscored_does_nothing(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id)
    _mk_final_work(db, regular_user.id, cycle.id, score=None)
    assert close_cycle(db, cycle) is False
    db.refresh(cycle)
    assert cycle.closed_at is None


def test_close_cycle_revision_final_does_not_close(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id)
    _mk_final_work(db, regular_user.id, cycle.id, score=82, needs_revision=True)
    assert close_cycle(db, cycle) is False
    db.refresh(cycle)
    assert cycle.closed_at is None


def test_close_cycle_already_closed_idempotent(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    original_closed_at = cycle.closed_at
    _mk_final_work(db, regular_user.id, cycle.id, score=70)
    assert close_cycle(db, cycle) is False
    db.refresh(cycle)
    assert cycle.closed_at == original_closed_at


def test_close_cycle_non_final_does_nothing(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id)
    w = Work(
        user_id=regular_user.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="05", year=2026, filename="stage.jpg",
        status="success", is_final=False, cycle_id=cycle.id, score=80,
    )
    db.add(w); db.commit(); db.refresh(w)
    assert close_cycle(db, cycle) is False
    db.refresh(cycle)
    assert cycle.closed_at is None


# ── Routes: /cabinet/scores удалён, /cycle/otrabotka редиректит ──────────────

def test_scores_route_removed(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/scores")
    assert resp.status_code == 404


def test_cycle_otrabotka_redirects_to_mock_tab(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/cycle/otrabotka", follow_redirects=False)
    assert resp.status_code == 302
    assert "/cabinet/cycle" in resp.headers["location"]


def test_cycle_probnik_redirects_to_mock_tab(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/cycle/probnik", follow_redirects=False)
    assert resp.status_code == 302
    assert "/cabinet/cycle" in resp.headers["location"]


def test_student_feedback_root_redirects_to_cycle_feedback_tab(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/feedback/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/cabinet/cycle" in resp.headers["location"]
    assert "tab=feedback" in resp.headers["location"]


# ── Cycle page: feedback tab visibility ──────────────────────────────────────

def test_cycle_page_only_feedback_tab_no_mock_tab(auth_client):
    """У ученика в Цикле Пробника осталась только вкладка «Обратная связь»."""
    client, _ = auth_client
    resp = client.get("/cabinet/cycle")
    assert resp.status_code == 200
    # Вкладка «Обратная связь» всегда присутствует, вкладки «Пробник» нет.
    assert 'data-tab="feedback"' in resp.text
    assert 'data-tab="mock"' not in resp.text


def test_feedback_cycle_shows_submitted_ticket_image(auth_client, db):
    """В диалоге цикла видна фотография билета, по которому сдана работа."""
    from app.models.exam_assignment import ExamAssignment, ExamTicket

    client, user = auth_client
    assignment = ExamAssignment(
        title="Пробник по рисунку", subject="Рисунок",
        created_by_id=user.id, status="published",
    )
    db.add(assignment)
    db.flush()
    ticket = ExamTicket(
        assignment_id=assignment.id, ticket_number=1, title="Натюрморт",
        image_s3_url="https://example.test/ticket.jpg",
        start_date=date.today(), end_date=date.today(), assign_to_all=True,
    )
    db.add(ticket)
    db.flush()
    cycle = ExamCycle(
        user_id=user.id, subject="Рисунок", ticket_id=ticket.id,
        started_at=date.today(),
    )
    db.add(cycle)
    db.flush()
    _mk_final_work(db, user.id, cycle.id)

    resp = client.get(f"/cabinet/feedback/{cycle.id}")

    assert resp.status_code == 200
    assert "Билет пробника" in resp.text
    assert ticket.image_s3_url in resp.text


def test_superadmin_feedback_cycle_shows_return_for_stages_button(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id, closed=False)
    _mk_final_work(db, regular_user.id, cycle.id)
    admin_session = session_factory(admin_user)
    client.cookies.set("session_id", admin_session.id)

    resp = client.get(f"/cabinet/superadmin/feedback/{cycle.id}")

    assert resp.status_code == 200
    assert 'id="dlg-student-revision-btn"' in resp.text
    assert "Вернуть ученику для этапов" in resp.text


def test_admin_feedback_cycle_shows_return_for_stages_button(
    client, regular_user, user_factory, session_factory, db
):
    admin = user_factory(vk_id=702_001, name="Admin", role_name="админ")
    cycle = _mk_cycle(db, regular_user.id, closed=False)
    _mk_final_work(db, regular_user.id, cycle.id)
    admin_session = session_factory(admin)
    client.cookies.set("session_id", admin_session.id)

    resp = client.get(f"/cabinet/admin/feedback/{cycle.id}")

    assert resp.status_code == 200
    assert 'id="dlg-student-revision-btn"' in resp.text
    assert 'class="dlg-head-actions"' in resp.text


def test_superadmin_feedback_cycle_shows_returned_to_student_status(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id, closed=False)
    final = _mk_final_work(db, regular_user.id, cycle.id)
    final.needs_revision = True
    db.commit()
    admin_session = session_factory(admin_user)
    client.cookies.set("session_id", admin_session.id)

    resp = client.get(f"/cabinet/superadmin/feedback/{cycle.id}")

    assert resp.status_code == 200
    assert "УЧЕНИК МОЖЕТ ДОГРУЗИТЬ ЭТАПЫ" in resp.text
    assert 'id="dlg-student-revision-btn"' not in resp.text


def test_cycle_page_feedback_tab_visible_with_open_cycle(auth_client, db):
    client, user = auth_client
    _mk_cycle(db, user.id, closed=False)
    resp = client.get("/cabinet/cycle")
    assert resp.status_code == 200
    assert 'data-tab="feedback"' in resp.text


# ── Dialog message POST ──────────────────────────────────────────────────────

def test_student_cannot_send_before_staff_message(auth_client, db):
    client, user = auth_client
    cycle = _mk_cycle(db, user.id)
    work = _mk_final_work(db, user.id, cycle.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "first message from student"},
    )
    assert resp.status_code == 403


def test_admin_can_send_first_message_and_student_can_reply(
    client, admin_user, regular_user, session_factory, db
):
    """Админ пишет первым → создаётся Feedback и сообщение → студент может ответить."""
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id)

    # Admin session
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "Привет, разбираем работу"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    fb = db.query(Feedback).filter(Feedback.work_id == work.id).first()
    assert fb is not None
    msgs = db.query(FeedbackMessage).filter(FeedbackMessage.feedback_id == fb.id).all()
    assert len(msgs) == 1
    assert msgs[0].sender_role == "superadmin"
    assert msgs[0].text == "Привет, разбираем работу"

    # Now student replies
    student_sess = session_factory(regular_user)
    client.cookies.set("session_id", student_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "Понял, переделаю"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    msgs = db.query(FeedbackMessage).filter(FeedbackMessage.feedback_id == fb.id).order_by(FeedbackMessage.id).all()
    assert len(msgs) == 2
    assert msgs[1].sender_role == "student"


def test_staff_structured_first_feedback_composed_into_one_message(
    client, admin_user, regular_user, session_factory, db
):
    """4 пункта первой обратной связи склеиваются в одно сообщение с заголовками."""
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={
            "impression": "В целом крепко",
            "good": "Композиция сильная",
            "strengthen": "Тон в тенях",
            "recommendations": "Поработать над краями",
        },
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    fb = db.query(Feedback).filter(Feedback.work_id == work.id).first()
    msgs = db.query(FeedbackMessage).filter(FeedbackMessage.feedback_id == fb.id).all()
    assert len(msgs) == 1
    text = msgs[0].text
    assert "Общее впечатление:\nВ целом крепко" in text
    assert "Что хорошо:\nКомпозиция сильная" in text
    assert "Что улучшить:\nТон в тенях" in text
    assert "Рекомендации:\nПоработать над краями" in text


def test_first_feedback_saves_intermediate_score_separately_from_final(
    client, admin_user, regular_user, session_factory, db
):
    """Балл для отработки хранится на цикле и не заменяет финальный балл работы."""
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id, score=82)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)

    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={
            "impression": "Есть что доработать",
            "intermediate_score": "64",
        },
        headers={"Accept": "application/json"},
    )

    assert resp.status_code == 200
    db.refresh(cycle)
    db.refresh(work)
    assert float(cycle.intermediate_score) == 64
    assert float(work.score) == 82


def test_feedback_dialog_shows_intermediate_score_for_work_on(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id)
    cycle.intermediate_score = 67
    _mk_final_work(db, regular_user.id, cycle.id)
    db.commit()
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)

    resp = client.get(f"/cabinet/superadmin/feedback/{cycle.id}")

    assert resp.status_code == 200
    assert "Промежуточный балл для отработки" in resp.text
    assert "67 / 100" in resp.text


def test_intermediate_score_must_be_between_zero_and_hundred(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)

    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"impression": "Проверка", "intermediate_score": "101"},
        headers={"Accept": "application/json"},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "Промежуточный балл должен быть от 0 до 100"
    db.refresh(cycle)
    assert cycle.intermediate_score is None


def test_staff_structured_partial_only_filled_sections(
    client, admin_user, regular_user, session_factory, db
):
    """Пустые пункты структурной формы не попадают в сообщение."""
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"impression": "Хорошее начало", "good": "  ", "strengthen": "", "recommendations": ""},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    fb = db.query(Feedback).filter(Feedback.work_id == work.id).first()
    msg = db.query(FeedbackMessage).filter(FeedbackMessage.feedback_id == fb.id).first()
    assert msg.text == "Общее впечатление:\nХорошее начало"


def test_staff_structured_all_empty_400(
    client, admin_user, regular_user, session_factory, db
):
    """Пустая структурная форма без фото → 400."""
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"impression": " ", "good": "", "strengthen": "", "recommendations": ""},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 400


def test_message_without_text_or_photo_400(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "  "},  # whitespace-only and no photo
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 400


def test_feedback_photo_input_limit_is_25_mb(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)

    assert feedback_service.MAX_FEEDBACK_PHOTO_INPUT_SIZE == 25 * 1024 * 1024
    with patch.object(feedback_service, "MAX_FEEDBACK_PHOTO_INPUT_SIZE", 10):
        resp = client.post(
            f"/cabinet/feedback/{work.id}/message",
            files={"photo": ("large.jpg", b"x" * 11, "image/jpeg")},
            headers={"Accept": "application/json"},
        )

    assert resp.status_code == 413
    assert resp.json()["detail"] == "Фото больше 25 МБ"


def test_feedback_form_explains_photo_limit_and_compression(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id)
    _mk_final_work(db, regular_user.id, cycle.id)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)

    resp = client.get(f"/cabinet/superadmin/feedback/{cycle.id}")

    assert resp.status_code == 200
    assert "фото до 25 МБ" in resp.text
    assert "будет сжато автоматически" in resp.text
    assert "MAX_FEEDBACK_PHOTO_SIZE = 25 * 1024 * 1024" in resp.text


def test_feedback_photo_above_old_10_mb_limit_is_compressed_and_uploaded(
    client, db, user_factory, session_factory
):
    curator = user_factory(vk_id=950001, name="Curator", role_name="куратор")
    student = user_factory(vk_id=950002, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.add(student)
    db.commit()
    cycle = _mk_cycle(db, student.id)
    work = _mk_final_work(db, student.id, cycle.id)
    session = session_factory(curator)
    client.cookies.set("session_id", session.id)

    source = b"x" * (10 * 1024 * 1024 + 1)
    with (
        patch.object(feedback_service, "compress_image", return_value=b"compressed-jpeg") as compress,
        patch.object(
            feedback_service.s3_service,
            "upload_to_s3",
            return_value="https://s3.example.com/feedback/photo.jpg",
        ),
    ):
        resp = client.post(
            f"/cabinet/feedback/{work.id}/message",
            files={"photo": ("phone-photo.jpg", source, "image/jpeg")},
            headers={"Accept": "application/json"},
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["photo_s3_url"] == "https://s3.example.com/feedback/photo.jpg"
    assert len(compress.call_args.args[0]) == len(source)


def test_feedback_photo_must_fit_stored_limit_after_compression(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)

    with (
        patch.object(feedback_service, "MAX_FEEDBACK_PHOTO_STORED_SIZE", 10),
        patch.object(feedback_service, "compress_image", return_value=b"x" * 11),
        patch.object(feedback_service.s3_service, "upload_to_s3") as upload,
    ):
        resp = client.post(
            f"/cabinet/feedback/{work.id}/message",
            files={"photo": ("photo.jpg", b"source", "image/jpeg")},
            headers={"Accept": "application/json"},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Фото после сжатия превышает 10 МБ"
    upload.assert_not_called()


def test_feedback_video_uploaded_without_compression(
    client, db, user_factory, session_factory
):
    curator = user_factory(vk_id=951001, name="Curator", role_name="куратор")
    student = user_factory(vk_id=951002, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.add(student)
    db.commit()
    cycle = _mk_cycle(db, student.id)
    work = _mk_final_work(db, student.id, cycle.id)
    session = session_factory(curator)
    client.cookies.set("session_id", session.id)

    source = b"\x00\x01\x02" * 1000
    with (
        patch.object(feedback_service, "compress_image") as compress,
        patch.object(
            feedback_service.s3_service,
            "upload_to_s3",
            return_value="https://s3.example.com/feedback/clip.mp4",
        ) as upload,
    ):
        resp = client.post(
            f"/cabinet/feedback/{work.id}/message",
            files={"video": ("razbor.mp4", source, "video/mp4")},
            headers={"Accept": "application/json"},
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["video_s3_url"] == "https://s3.example.com/feedback/clip.mp4"
    # видео не сжимается и уходит как есть
    compress.assert_not_called()
    assert upload.call_args.args[1] == source


def test_feedback_video_bad_format_rejected(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)

    with patch.object(feedback_service.s3_service, "upload_to_s3") as upload:
        resp = client.post(
            f"/cabinet/feedback/{work.id}/message",
            files={"video": ("notes.txt", b"hello", "text/plain")},
            headers={"Accept": "application/json"},
        )

    assert resp.status_code == 422
    upload.assert_not_called()


def test_student_cannot_message_in_closed_cycle(
    client, admin_user, regular_user, session_factory, db
):
    """Ученику запись в закрытый цикл (балл уже выставлен) недоступна."""
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    work = _mk_final_work(db, regular_user.id, cycle.id, score=80)
    student_sess = session_factory(regular_user)
    client.cookies.set("session_id", student_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "should fail"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 403


def test_staff_can_message_in_closed_cycle(
    client, admin_user, regular_user, session_factory, db
):
    """Staff (админ/SA) может оставить обратную связь и после простановки балла —
    закрытый цикл больше не блокирует запись для staff."""
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    work = _mk_final_work(db, regular_user.id, cycle.id, score=80)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "Разбор после балла"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_curator_can_give_feedback_on_closed_cycle(
    client, db, user_factory, session_factory
):
    """Куратор даёт обратную связь даже когда цикл закрыт (балл выставил админ).
    Балл и обратная связь — разные функции: по правам куратор не ставит балл,
    но обязан мочь дать ОС."""
    curator = user_factory(vk_id=940001, name="Curator", role_name="куратор")
    student = user_factory(vk_id=940002, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.add(student)
    db.commit()
    cycle = _mk_cycle(db, student.id, closed=True)
    work = _mk_final_work(db, student.id, cycle.id, score=80)

    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "Разбор работы куратором"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    fb = db.query(Feedback).filter(Feedback.work_id == work.id).first()
    assert fb is not None
    msgs = db.query(FeedbackMessage).filter(FeedbackMessage.feedback_id == fb.id).all()
    assert len(msgs) == 1
    assert msgs[0].sender_role == "curator"
    assert msgs[0].text == "Разбор работы куратором"


def test_curator_cannot_post_feedback_for_foreign_student(
    client, db, user_factory, session_factory
):
    owner = user_factory(vk_id=930001, name="Owner", role_name="куратор")
    other = user_factory(vk_id=930002, name="Other", role_name="куратор")
    student = user_factory(vk_id=930003, name="Student", role_name="ученик")
    student.curator_id = owner.id
    db.add(student)
    db.commit()
    cycle = _mk_cycle(db, student.id)
    work = _mk_final_work(db, student.id, cycle.id)

    sess = session_factory(other)
    client.cookies.set("session_id", sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "should fail"},
        headers={"Accept": "application/json"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Это не ваш студент"


@pytest.mark.parametrize(
    "role_name,should_allow",
    [
        ("куратор", True),
        ("модератор", False),
        ("админ", True),
        ("суперадмин", True),
    ],
)
def test_feedback_write_gate_matches_permission_table_by_rank(
    client, db, user_factory, session_factory, role_name, should_allow
):
    """Locks in current feedback.py:313 allow/deny per role before migrating the
    permission check to a rank-equivalent (моderator must stay denied — it has
    no `feedback.write` in ROLE_PERMISSIONS despite role_from_rank mapping it
    to the same sender_role as curator)."""
    actor = user_factory(vk_id=940001, name="Actor", role_name=role_name)
    student = user_factory(vk_id=940002, name="Student", role_name="ученик")
    if role_name == "куратор":
        student.curator_id = actor.id
        db.add(student)
        db.commit()
    cycle = _mk_cycle(db, student.id)
    work = _mk_final_work(db, student.id, cycle.id)

    sess = session_factory(actor)
    client.cookies.set("session_id", sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "test message"},
        headers={"Accept": "application/json"},
    )

    if should_allow:
        assert resp.status_code == 200, resp.text
    else:
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Нет прав на запись feedback"


# ── Единое окно диалога на цикл (редизайн 2026-06-02) ─────────────────────────

def _mk_otrabotka_final(db, user_id, cycle_id, *, score=None, attempt=1):
    from app.models.work import WORK_TYPE_RETAKE
    w = Work(
        user_id=user_id, work_type=WORK_TYPE_RETAKE,
        month="05", year=2026, filename=f"otr-{attempt}.jpg",
        subject="Drawing", status="success", is_final=True,
        cycle_id=cycle_id, attempt_number=attempt, score=score,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


def _mk_staff_message(db, work_id, *, curator_id, sender_role, text):
    fb = db.query(Feedback).filter(Feedback.work_id == work_id).first()
    if fb is None:
        fb = Feedback(work_id=work_id, curator_id=curator_id)
        db.add(fb)
        db.commit()
        db.refresh(fb)
    m = FeedbackMessage(
        feedback_id=fb.id, sender_id=curator_id, sender_role=sender_role, text=text,
    )
    db.add(m)
    db.commit()
    return fb


def test_dialog_single_window_targets_probnik_final(
    client, admin_user, regular_user, session_factory, db
):
    """Цикл с финалкой Пробника + Отработки → одно окно, форма целится в финалку
    Пробника (mock_exam), нумерация попыток убрана, обе финалки видны как контекст."""
    cycle = _mk_cycle(db, regular_user.id)
    probnik = _mk_final_work(db, regular_user.id, cycle.id, attempt=1, score=70)
    otrabotka = _mk_otrabotka_final(db, regular_user.id, cycle.id, attempt=1, score=85)
    _mk_staff_message(db, probnik.id, curator_id=admin_user.id, sender_role="superadmin",
                      text="Разбор пробника")

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.get(f"/cabinet/superadmin/feedback/{cycle.id}")
    assert resp.status_code == 200
    html = resp.text
    # Одно диалоговое окно, без нумерации попыток
    assert html.count('class="dlg-attempt"') == 1
    assert "Попытка №" not in html
    assert "Разбор пробника" in html
    # Форма сообщений целится в финалку Пробника, не в Отработку
    assert f'/cabinet/feedback/{probnik.id}/message' in html
    assert f'/cabinet/feedback/{otrabotka.id}/message' not in html


def test_student_can_reply_in_multi_final_cycle(
    client, admin_user, regular_user, session_factory, db
):
    """Студент может ответить в единственном окне после ОС staff, даже когда в цикле
    есть финалка Отработки (форма и POST-гейт совпадают на финалке Пробника)."""
    cycle = _mk_cycle(db, regular_user.id)
    probnik = _mk_final_work(db, regular_user.id, cycle.id, attempt=1)
    _mk_otrabotka_final(db, regular_user.id, cycle.id, attempt=1)
    _mk_staff_message(db, probnik.id, curator_id=admin_user.id, sender_role="superadmin",
                      text="Жду ответ")

    student_sess = session_factory(regular_user)
    client.cookies.set("session_id", student_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{probnik.id}/message",
        data={"text": "Понял, спасибо"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_staff_probnik_calendar_renders_via_partial(
    client, admin_user, regular_user, session_factory, db
):
    """Staff-календарь Пробника (cabinet_cycle_calendar.html) после рефактора на
    общий партиал cycle_day_calendar.html рендерится без ошибок Jinja."""
    cycle = _mk_cycle(db, regular_user.id)
    _mk_final_work(db, regular_user.id, cycle.id, score=88)

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.get(f"/cabinet/staff/cycle/probnik/{regular_user.id}")
    assert resp.status_code == 200
    # Партиал подключён и инициализирован (CYCCAL), карточки предметов рендерятся.
    assert "CYCCAL.init" in resp.text
    assert "subj-card" in resp.text


def test_curator_cannot_open_foreign_student_probnik_calendar(
    client, db, user_factory, session_factory
):
    owner = user_factory(vk_id=930004, name="Owner", role_name="куратор")
    other = user_factory(vk_id=930005, name="Other", role_name="куратор")
    student = user_factory(vk_id=930006, name="Student", role_name="ученик")
    student.curator_id = owner.id
    db.add(student)
    db.commit()

    sess = session_factory(other)
    client.cookies.set("session_id", sess.id)
    resp = client.get(
        f"/cabinet/staff/cycle/probnik/{student.id}",
        follow_redirects=False,
    )

    assert resp.status_code == 403
    assert "Не ваш студент" in resp.text


# ── POST /cabinet/feedback/{cycle_id}/close (ручное закрытие цикла) ──────────

def test_close_cycle_requires_score(client, admin_user, regular_user, session_factory, db):
    """Закрыть цикл без выставленного балла финалке нельзя — 409."""
    cycle = _mk_cycle(db, regular_user.id)
    _mk_final_work(db, regular_user.id, cycle.id, score=None)

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(f"/cabinet/feedback/{cycle.id}/close", headers={"Accept": "application/json"})
    assert resp.status_code == 409
    db.refresh(cycle)
    assert cycle.closed_at is None


def test_close_cycle_closes_and_releases_lock(client, admin_user, regular_user, session_factory, db):
    """Балл выставлен → закрытие цикла ставит closed_at и снимает MockExamLock."""
    cycle = _mk_cycle(db, regular_user.id)
    _mk_final_work(db, regular_user.id, cycle.id, score=82)
    db.add(MockExamLock(
        user_id=regular_user.id, subject="Drawing", is_locked=True,
    ))
    db.commit()

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(f"/cabinet/feedback/{cycle.id}/close", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    db.refresh(cycle)
    assert cycle.closed_at is not None

    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == regular_user.id, MockExamLock.subject == "Drawing"
    ).first()
    assert lock.is_locked is False


def test_close_cycle_curator_can_close_own_student(client, db, user_factory, session_factory):
    """Куратор (не только админ/SA) может закрыть цикл своего ученика."""
    curator = user_factory(vk_id=941001, name="Curator", role_name="куратор")
    student = user_factory(vk_id=941002, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.add(student)
    db.commit()
    cycle = _mk_cycle(db, student.id)
    _mk_final_work(db, student.id, cycle.id, score=75)

    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    resp = client.post(f"/cabinet/feedback/{cycle.id}/close", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    db.refresh(cycle)
    assert cycle.closed_at is not None


def test_close_cycle_curator_cannot_close_foreign_student(client, db, user_factory, session_factory):
    owner = user_factory(vk_id=941003, name="Owner", role_name="куратор")
    other = user_factory(vk_id=941004, name="Other", role_name="куратор")
    student = user_factory(vk_id=941005, name="Student", role_name="ученик")
    student.curator_id = owner.id
    db.add(student)
    db.commit()
    cycle = _mk_cycle(db, student.id)
    _mk_final_work(db, student.id, cycle.id, score=75)

    sess = session_factory(other)
    client.cookies.set("session_id", sess.id)
    resp = client.post(f"/cabinet/feedback/{cycle.id}/close", headers={"Accept": "application/json"})
    assert resp.status_code == 403
    db.refresh(cycle)
    assert cycle.closed_at is None


def test_close_cycle_already_closed_400(client, admin_user, regular_user, session_factory, db):
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    _mk_final_work(db, regular_user.id, cycle.id, score=80)

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(f"/cabinet/feedback/{cycle.id}/close", headers={"Accept": "application/json"})
    assert resp.status_code == 400


def test_student_cannot_close_cycle(auth_client, db):
    client, user = auth_client
    cycle = _mk_cycle(db, user.id)
    _mk_final_work(db, user.id, cycle.id, score=80)

    resp = client.post(f"/cabinet/feedback/{cycle.id}/close", headers={"Accept": "application/json"})
    assert resp.status_code == 403
    db.refresh(cycle)
    assert cycle.closed_at is None


# ── Scoring no longer auto-closes the cycle ──────────────────────────────────

def test_scoring_work_does_not_close_cycle(client, admin_user, regular_user, session_factory, db):
    """POST .../works/{id}/score выставляет балл, но НЕ закрывает цикл —
    закрытие теперь отдельный ручной шаг (POST /cabinet/feedback/{cycle_id}/close)."""
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id, score=None)
    db.add(MockExamLock(
        user_id=regular_user.id, subject="Drawing", is_locked=True,
    ))
    db.commit()

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(
        f"/cabinet/students/{regular_user.id}/works/{work.id}/score",
        data={"score": "80", "comment": "", "tab": "mock-exams"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    db.refresh(cycle)
    assert cycle.closed_at is None
    db.refresh(work)
    assert work.score == 80

    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == regular_user.id, MockExamLock.subject == "Drawing"
    ).first()
    assert lock.is_locked is True


def test_curator_cannot_load_foreign_student_cycles_json(
    client, db, user_factory, session_factory
):
    owner = user_factory(vk_id=930007, name="Owner", role_name="куратор")
    other = user_factory(vk_id=930008, name="Other", role_name="куратор")
    student = user_factory(vk_id=930009, name="Student", role_name="ученик")
    student.curator_id = owner.id
    db.add(student)
    db.commit()

    sess = session_factory(other)
    client.cookies.set("session_id", sess.id)
    resp = client.get(
        f"/cabinet/students/{student.id}/cycles",
        follow_redirects=False,
    )

    assert resp.status_code == 403
    assert "Не ваш студент" in resp.text


def test_curator_cannot_open_foreign_student_feedback_detail(
    client, db, user_factory, session_factory
):
    owner = user_factory(vk_id=930010, name="Owner", role_name="куратор")
    other = user_factory(vk_id=930011, name="Other", role_name="куратор")
    student = user_factory(vk_id=930012, name="Student", role_name="ученик")
    student.curator_id = owner.id
    db.add(student)
    db.commit()
    cycle = _mk_cycle(db, student.id)
    _mk_final_work(db, student.id, cycle.id)

    sess = session_factory(other)
    client.cookies.set("session_id", sess.id)
    resp = client.get(
        f"/cabinet/curator/feedback/{cycle.id}",
        follow_redirects=False,
    )

    assert resp.status_code == 403
    assert "Это не ваш студент" in resp.text


def test_staff_portfolio_json_returns_cycle_works_by_subject(
    client, admin_user, regular_user, session_factory, db
):
    """Staff-просмотр Портфолио ученика: секция «Пробные экзамены» отдаётся в том
    же формате (mock_works_by_subject), что и у ученика — единый дневной календарь
    для всех ролей. Финалка закрытого цикла попадает под свой предмет."""
    cycle = _mk_cycle(db, regular_user.id, subject="Рисунок", closed=True)
    w = Work(
        user_id=regular_user.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="05", year=2026, filename="closed-final.jpg", subject="Рисунок",
        status="success", is_final=True, cycle_id=cycle.id, attempt_number=1, score=77,
    )
    db.add(w)
    db.commit()

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.get(f"/cabinet/students/{regular_user.id}/portfolio")
    assert resp.status_code == 200
    data = resp.json()
    # Новый формат — без помесячной плоской сетки.
    assert "mock_works_by_subject" in data
    assert "mock_by_month" not in data
    drawing = data["mock_works_by_subject"].get("Рисунок", [])
    assert any(item["filename"] == "closed-final.jpg" and item["score"] == 77 for item in drawing)


def test_portfolio_collector_includes_legacy_scored_mock_and_excludes_stages(
    regular_user, db
):
    """Портфолио → Пробные экзамены: closed_only должен показывать ОЦЕНЁННЫЕ
    финалы из обоих источников — нового flow (финал закрытого цикла) И легаси
    /upload/mock-exam (cycle_id IS NULL, is_final=false). Этапные (parent_work_id
    задан) и неоценённые работы не попадают."""
    from app.api.cabinet_student import _collect_cycle_works

    # 1) Легаси-пробник: оценён, но без цикла и без is_final — раньше был невидим.
    legacy = Work(
        user_id=regular_user.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="03", year=2026, filename="legacy-mock.jpg", subject="Рисунок",
        status="success", is_final=False, cycle_id=None, score=64,
    )
    # 2) Новый flow: финал закрытого цикла, оценён.
    cycle = _mk_cycle(db, regular_user.id, subject="Композиция", closed=True)
    final = Work(
        user_id=regular_user.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="06", year=2026, filename="new-final.jpg", subject="Композиция",
        status="success", is_final=True, cycle_id=cycle.id, attempt_number=1, score=88,
    )
    db.add_all([legacy, final])
    db.flush()
    # 3) Этап финала — оценок не имеет, parent_work_id задан → не в Портфолио.
    stage = Work(
        user_id=regular_user.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="06", year=2026, filename="stage.jpg", subject="Композиция",
        status="success", is_final=False, cycle_id=cycle.id, parent_work_id=final.id,
    )
    # 4) Неоценённый легаси-пробник → не показываем.
    unscored = Work(
        user_id=regular_user.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="04", year=2026, filename="unscored.jpg", subject="Рисунок",
        status="success", is_final=False, cycle_id=None, score=None,
    )
    db.add_all([stage, unscored])
    db.commit()

    res = _collect_cycle_works(db, regular_user.id, WORK_TYPE_MOCK_EXAM, closed_only=True)
    names = {w["filename"] for works in res.values() for w in works}
    assert "legacy-mock.jpg" in names      # легаси-сирота теперь виден
    assert "new-final.jpg" in names        # новый финал виден
    assert "stage.jpg" not in names        # этап скрыт
    assert "unscored.jpg" not in names     # неоценённый скрыт


def test_staff_students_page_wires_mock_calendar(
    client, admin_user, session_factory, db
):
    """Staff-страница /cabinet/students подключает библиотеку CYCCAL и обвязку
    инициализации календаря «Пробные экзамены» в портфолио ученика."""
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.get("/cabinet/students")
    assert resp.status_code == 200
    # Библиотека календаря подключена (window.CYCCAL) и инициализируется для staff.
    assert "window.CYCCAL" in resp.text
    assert "portfolio-mock-cal" in resp.text
    assert "staff-portfolio-mock" in resp.text


def test_staff_cycles_page_lists_only_open_with_identity(
    client, admin_user, regular_user, session_factory, db
):
    # Закрытый цикл не должен попадать в список открытых.
    closed = _mk_cycle(db, regular_user.id, subject="Композиция", closed=True)
    _mk_final_work(db, regular_user.id, closed.id, score=88)
    # Открытый цикл — отображается с именем, @username и тегами.
    regular_user.tg_username = "ivanp"
    db.commit()
    _mk_cycle(db, regular_user.id, subject="Рисунок", closed=False)

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.get("/cabinet/staff/cycles")

    assert resp.status_code == 200
    # Закрытый цикл скрыт: ровно одна строка-цикл, без бейджа балла.
    assert resp.text.count('class="cyc-row"') == 1
    assert "88 / 100" not in resp.text
    # Открытый цикл виден с идентификацией и тегами (имя, @username, тариф, период).
    assert "Рисунок" in resp.text
    assert "@ivanp" in resp.text
    assert "Test Student" in resp.text
    assert "УВЕРЕННЫЙ" in resp.text
    assert "10-14" in resp.text


@pytest.mark.parametrize("role_name", ["куратор", "админ", "суперадмин"])
def test_staff_cycles_page_has_subject_and_search_filters(
    client, regular_user, user_factory, session_factory, db, role_name
):
    """Фильтр по предмету + поиск доступны всем staff-ролям (curator/admin/SA)."""
    regular_user.tg_username = "ivanp"
    _mk_cycle(db, regular_user.id, subject="Рисунок", closed=False)
    staff = user_factory(vk_id=770_001, name="Staff", is_admin=(role_name != "куратор"),
                         role_name=role_name)
    if role_name == "куратор":
        # Куратор видит только своих учеников — привязываем.
        regular_user.curator_id = staff.id
    db.commit()

    sess = session_factory(staff)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/cabinet/staff/cycles")

    assert resp.status_code == 200
    # Фильтр по типу пробника.
    assert 'class="cyc-subj-pill"' in resp.text or "cyc-subj-pill" in resp.text
    assert 'data-subject="Рисунок"' in resp.text
    assert 'data-subject="Композиция"' in resp.text
    # Поиск по имени/username.
    assert 'id="cyc-search"' in resp.text
    # Строка несёт data-атрибуты для клиентской фильтрации.
    assert 'data-name="' in resp.text
    assert 'data-username="ivanp"' in resp.text
    assert "function filterCycles" in resp.text


def test_staff_cycles_archive_shows_closed_for_admin(
    client, admin_user, regular_user, session_factory, db
):
    """?status=archive: админ/SA видят закрытые циклы с баллом финалки."""
    closed = _mk_cycle(db, regular_user.id, subject="Композиция", closed=True)
    _mk_final_work(db, regular_user.id, closed.id, score=88)
    # Открытый цикл в архив не попадает.
    _mk_cycle(db, regular_user.id, subject="Рисунок", closed=False)

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.get("/cabinet/staff/cycles?status=archive")

    assert resp.status_code == 200
    # Ровно один закрытый цикл (открытый исключён), с баллом финалки.
    # Балл «88 / 100» рендерится только в строке архива → open-цикл сюда не попал.
    assert resp.text.count('class="cyc-row"') == 1
    assert "88 / 100" in resp.text
    # Вкладка «Архив» доступна админу.
    assert 'href="/cabinet/staff/cycles?status=archive"' in resp.text


def test_staff_cycles_archive_hidden_from_curator(
    client, regular_user, user_factory, session_factory, db
):
    """Куратор не видит вкладку «Архив»; ?status=archive отдаёт открытый список."""
    closed = _mk_cycle(db, regular_user.id, subject="Композиция", closed=True)
    _mk_final_work(db, regular_user.id, closed.id, score=88)
    _mk_cycle(db, regular_user.id, subject="Рисунок", closed=False)
    curator = user_factory(vk_id=770_002, name="Curator", role_name="куратор")
    regular_user.curator_id = curator.id
    db.commit()

    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/cabinet/staff/cycles?status=archive")

    assert resp.status_code == 200
    # Нет вкладки архива и нет закрытого цикла — куратору отдан открытый список.
    assert 'href="/cabinet/staff/cycles?status=archive"' not in resp.text
    assert "88 / 100" not in resp.text
    assert "Рисунок" in resp.text


def test_staff_cycles_open_has_fb_filter_for_admin(
    client, admin_user, regular_user, session_factory, db
):
    """Открытые циклы: админ/SA видят фильтр ОС (Нет ОС / Есть ОС), строки несут data-fb."""
    _mk_cycle(db, regular_user.id, subject="Рисунок", closed=False)

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.get("/cabinet/staff/cycles")

    assert resp.status_code == 200
    assert 'data-fb="none"' in resp.text  # пилюля «Нет ОС»
    assert 'data-fb="has"' in resp.text   # пилюля «Есть ОС»
    assert "function selectFb" in resp.text
    # Строка несёт счётчик ОС для клиентской фильтрации.
    assert 'data-fb="0"' in resp.text


def test_staff_cycles_open_fb_filter_shown_to_curator(
    client, regular_user, user_factory, session_factory, db
):
    """Куратор видит фильтр ОС в открытых циклах — как админ/SA (фильтры «как у всех»)."""
    _mk_cycle(db, regular_user.id, subject="Рисунок", closed=False)
    curator = user_factory(vk_id=770_003, name="Curator", role_name="куратор")
    regular_user.curator_id = curator.id
    db.commit()

    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/cabinet/staff/cycles")

    assert resp.status_code == 200
    # Кнопки фильтра ОС теперь рендерятся куратору в открытых циклах.
    assert 'data-fb="none"' in resp.text
    assert 'onclick="selectFb(this)"' in resp.text


def test_cycle_page_shows_close_score_badge_for_closed_cycle(auth_client, db):
    client, user = auth_client
    cycle = _mk_cycle(db, user.id, closed=True)
    _mk_final_work(db, user.id, cycle.id, score=88)

    resp = client.get("/cabinet/cycle")

    assert resp.status_code == 200
    assert "88 / 100" in resp.text
    assert "fb-score-badge score-green" in resp.text


# ── Superadmin: удаление открытого цикла (ученик ошибся при отправке) ─────────

def test_superadmin_deletes_open_cycle_cascades(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id, subject="Drawing", closed=False)
    work = _mk_final_work(db, regular_user.id, cycle.id)
    work.s3_path = "probniki/1/1/attempt-1/final/x.jpg"
    fb = Feedback(work_id=work.id, curator_id=admin_user.id)
    db.add(fb)
    db.commit()
    db.refresh(fb)
    db.add(FeedbackMessage(
        feedback_id=fb.id, sender_id=admin_user.id, sender_role="superadmin",
        text="разбор", photo_s3_path="probniki/1/1/attempt-1/msg.jpg",
    ))
    db.add(MockExamLock(
        user_id=regular_user.id, subject="Drawing", is_locked=True,
        locked_at=datetime.now(timezone.utc),
    ))
    db.commit()

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    with patch("app.services.s3.delete_from_s3") as s3_del:
        resp = client.post(f"/cabinet/superadmin/feedback/{cycle.id}/delete",
                           headers={"Accept": "application/json"})

    assert resp.status_code == 200
    # Цикл и все дочерние строки удалены (FK в SQLite выключены — проверка вместо них).
    assert db.query(ExamCycle).filter(ExamCycle.id == cycle.id).first() is None
    assert db.query(Work).filter(Work.cycle_id == cycle.id).count() == 0
    assert db.query(Feedback).filter(Feedback.id == fb.id).first() is None
    assert db.query(FeedbackMessage).filter(FeedbackMessage.feedback_id == fb.id).count() == 0
    # Блокировка пробника снята → ученик может пересдать.
    lock = db.query(MockExamLock).filter(MockExamLock.user_id == regular_user.id).first()
    assert lock is not None and lock.is_locked is False
    # S3-файлы (работа + фото сообщения) ушли на очистку.
    deleted_paths = {c.args[0] for c in s3_del.call_args_list}
    assert "probniki/1/1/attempt-1/final/x.jpg" in deleted_paths
    assert "probniki/1/1/attempt-1/msg.jpg" in deleted_paths


def test_superadmin_cannot_delete_closed_cycle(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id, closed=True)

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(f"/cabinet/superadmin/feedback/{cycle.id}/delete",
                       headers={"Accept": "application/json"})

    assert resp.status_code == 400
    assert db.query(ExamCycle).filter(ExamCycle.id == cycle.id).first() is not None


def test_non_superadmin_cannot_delete_cycle(
    client, regular_user, user_factory, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id, closed=False)
    admin = user_factory(vk_id=888_001, name="Admin Rank4", is_admin=True, role_name="админ")

    admin_sess = session_factory(admin)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(f"/cabinet/superadmin/feedback/{cycle.id}/delete",
                       headers={"Accept": "application/json"})

    assert resp.status_code == 403
    assert db.query(ExamCycle).filter(ExamCycle.id == cycle.id).first() is not None


def test_superadmin_delete_missing_cycle_404(
    client, admin_user, session_factory, db
):
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post("/cabinet/superadmin/feedback/999999/delete",
                       headers={"Accept": "application/json"})
    assert resp.status_code == 404


# ── Service layer: reopen_cycle (зеркало close_cycle) ────────────────────────

def test_reopen_cycle_closed_reopens_and_relocks(db, regular_user):
    """Закрытый цикл → reopen сбрасывает closed_at и возвращает блокировку."""
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    db.add(MockExamLock(
        user_id=regular_user.id, subject="Drawing", is_locked=False,
        unlocked_at=datetime.now(timezone.utc),
    ))
    db.commit()
    assert reopen_cycle(db, cycle) is True
    db.refresh(cycle)
    assert cycle.closed_at is None
    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == regular_user.id, MockExamLock.subject == "Drawing"
    ).first()
    assert lock.is_locked is True
    assert lock.unlocked_at is None


def test_reopen_cycle_open_does_nothing(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id, closed=False)
    assert reopen_cycle(db, cycle) is False
    db.refresh(cycle)
    assert cycle.closed_at is None


def test_reopen_cycle_no_lock_row_does_not_create(db, regular_user):
    """Зеркало close: переоткрытие не создаёт lock-строку, если её нет."""
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    assert reopen_cycle(db, cycle) is True
    db.refresh(cycle)
    assert cycle.closed_at is None
    assert db.query(MockExamLock).filter(
        MockExamLock.user_id == regular_user.id
    ).first() is None


# ── Route: POST /cabinet/superadmin/feedback/{cycle_id}/reopen ───────────────

def test_superadmin_reopen_closed_cycle(client, admin_user, regular_user, session_factory, db):
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    db.add(MockExamLock(
        user_id=regular_user.id, subject="Drawing", is_locked=False,
        unlocked_at=datetime.now(timezone.utc),
    ))
    db.commit()

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(f"/cabinet/superadmin/feedback/{cycle.id}/reopen",
                       headers={"Accept": "application/json"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    db.refresh(cycle)
    assert cycle.closed_at is None
    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == regular_user.id, MockExamLock.subject == "Drawing"
    ).first()
    assert lock.is_locked is True


def test_reopen_open_cycle_400(client, admin_user, regular_user, session_factory, db):
    cycle = _mk_cycle(db, regular_user.id, closed=False)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(f"/cabinet/superadmin/feedback/{cycle.id}/reopen",
                       headers={"Accept": "application/json"})
    assert resp.status_code == 400
    db.refresh(cycle)
    assert cycle.closed_at is None  # остался открытым, без побочных эффектов


def test_reopen_blocked_when_other_open_cycle_409(
    client, admin_user, regular_user, session_factory, db
):
    """Если у ученика уже есть другой открытый цикл по тому же предмету — 409."""
    closed = _mk_cycle(db, regular_user.id, subject="Drawing", closed=True)
    _mk_cycle(db, regular_user.id, subject="Drawing", closed=False)

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(f"/cabinet/superadmin/feedback/{closed.id}/reopen",
                       headers={"Accept": "application/json"})
    assert resp.status_code == 409
    db.refresh(closed)
    assert closed.closed_at is not None  # остался закрытым


def test_non_superadmin_cannot_reopen_cycle(
    client, regular_user, user_factory, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    admin = user_factory(vk_id=889_001, name="Admin Rank4", is_admin=True, role_name="админ")

    admin_sess = session_factory(admin)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(f"/cabinet/superadmin/feedback/{cycle.id}/reopen",
                       headers={"Accept": "application/json"})
    assert resp.status_code == 403
    db.refresh(cycle)
    assert cycle.closed_at is not None


def test_reopen_missing_cycle_404(client, admin_user, session_factory, db):
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post("/cabinet/superadmin/feedback/999999/reopen",
                       headers={"Accept": "application/json"})
    assert resp.status_code == 404


# ── Возврат закрытого цикла куратору на изменение ОС ─────────────────────────

def _last_msg(db, feedback_id):
    return (
        db.query(FeedbackMessage)
        .filter(FeedbackMessage.feedback_id == feedback_id)
        .order_by(FeedbackMessage.id.desc())
        .first()
    )


def test_return_to_curator_sets_flag(client, admin_user, db, user_factory, session_factory):
    """SA возвращает закрытый цикл с ОС → revision_requested_at установлен, цикл остаётся закрыт."""
    curator = user_factory(vk_id=960001, name="Curator", role_name="куратор")
    student = user_factory(vk_id=960002, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.commit()
    cycle = _mk_cycle(db, student.id, closed=True)
    final = _mk_final_work(db, student.id, cycle.id, score=80)
    _mk_staff_message(db, final.id, curator_id=curator.id, sender_role="curator", text="ОС")

    sess = session_factory(admin_user)
    client.cookies.set("session_id", sess.id)
    resp = client.post(f"/cabinet/superadmin/feedback/{cycle.id}/return-to-curator",
                       headers={"Accept": "application/json"})
    assert resp.status_code == 200
    db.refresh(cycle)
    assert cycle.revision_requested_at is not None
    assert cycle.closed_at is not None  # цикл остаётся закрытым


def test_return_to_curator_open_cycle_allowed(client, admin_user, regular_user, session_factory, db):
    """Возврат доступен на любом цикле — в т.ч. открытом (если есть ОС для правки)."""
    cycle = _mk_cycle(db, regular_user.id, closed=False)
    final = _mk_final_work(db, regular_user.id, cycle.id, score=80)
    _mk_staff_message(db, final.id, curator_id=admin_user.id, sender_role="superadmin", text="ОС")
    sess = session_factory(admin_user)
    client.cookies.set("session_id", sess.id)
    resp = client.post(f"/cabinet/superadmin/feedback/{cycle.id}/return-to-curator",
                       headers={"Accept": "application/json"})
    assert resp.status_code == 200
    db.refresh(cycle)
    assert cycle.revision_requested_at is not None
    assert cycle.closed_at is None  # статус не изменился — остался открытым


def test_return_to_curator_no_feedback_rejected(client, admin_user, regular_user, session_factory, db):
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    _mk_final_work(db, regular_user.id, cycle.id, score=80)  # без Feedback
    sess = session_factory(admin_user)
    client.cookies.set("session_id", sess.id)
    resp = client.post(f"/cabinet/superadmin/feedback/{cycle.id}/return-to-curator",
                       headers={"Accept": "application/json"})
    assert resp.status_code == 400
    db.refresh(cycle)
    assert cycle.revision_requested_at is None


def test_curator_edits_own_message_during_revision(client, admin_user, db, user_factory, session_factory):
    """После возврата куратор-автор правит текст своего сообщения; ученику падает уведомление."""
    from app.models.notification import Notification
    curator = user_factory(vk_id=960011, name="Curator", role_name="куратор")
    student = user_factory(vk_id=960012, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.commit()
    cycle = _mk_cycle(db, student.id, closed=True)
    final = _mk_final_work(db, student.id, cycle.id, score=80)
    fb = _mk_staff_message(db, final.id, curator_id=curator.id, sender_role="curator", text="Старый текст")
    msg = _last_msg(db, fb.id)

    # SA возвращает на изменение.
    sa_sess = session_factory(admin_user)
    client.cookies.set("session_id", sa_sess.id)
    client.post(f"/cabinet/superadmin/feedback/{cycle.id}/return-to-curator",
                headers={"Accept": "application/json"})

    # Куратор правит своё сообщение.
    cur_sess = session_factory(curator)
    client.cookies.set("session_id", cur_sess.id)
    resp = client.post(f"/cabinet/feedback/message/{msg.id}/edit",
                       data={"text": "Исправленный текст"},
                       headers={"Accept": "application/json"})
    assert resp.status_code == 200
    db.refresh(msg)
    assert msg.text == "Исправленный текст"
    # Ученику ушло уведомление об обновлении ОС.
    notif = db.query(Notification).filter(Notification.user_id == student.id).first()
    assert notif is not None


def test_edit_message_blocked_without_revision_flag(client, db, user_factory, session_factory):
    """Без флага «на изменении» куратор не может править закрытую ОС — 403."""
    curator = user_factory(vk_id=960021, name="Curator", role_name="куратор")
    student = user_factory(vk_id=960022, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.commit()
    cycle = _mk_cycle(db, student.id, closed=True)
    final = _mk_final_work(db, student.id, cycle.id, score=80)
    fb = _mk_staff_message(db, final.id, curator_id=curator.id, sender_role="curator", text="ОС")
    msg = _last_msg(db, fb.id)

    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    resp = client.post(f"/cabinet/feedback/message/{msg.id}/edit",
                       data={"text": "Правка"}, headers={"Accept": "application/json"})
    assert resp.status_code == 403


def test_edit_message_only_own(client, admin_user, db, user_factory, session_factory):
    """Чужое сообщение править нельзя даже при активном флаге — 403."""
    author = user_factory(vk_id=960031, name="Author", role_name="куратор")
    other = user_factory(vk_id=960032, name="Other", role_name="куратор")
    student = user_factory(vk_id=960033, name="Student", role_name="ученик")
    student.curator_id = author.id
    db.commit()
    cycle = _mk_cycle(db, student.id, closed=True)
    final = _mk_final_work(db, student.id, cycle.id, score=80)
    fb = _mk_staff_message(db, final.id, curator_id=author.id, sender_role="curator", text="ОС")
    msg = _last_msg(db, fb.id)
    sa_sess = session_factory(admin_user)
    client.cookies.set("session_id", sa_sess.id)
    client.post(f"/cabinet/superadmin/feedback/{cycle.id}/return-to-curator",
                headers={"Accept": "application/json"})

    sess = session_factory(other)
    client.cookies.set("session_id", sess.id)
    resp = client.post(f"/cabinet/feedback/message/{msg.id}/edit",
                       data={"text": "Чужая правка"}, headers={"Accept": "application/json"})
    assert resp.status_code == 403
    db.refresh(msg)
    assert msg.text == "ОС"


def test_finish_revision_clears_flag(client, admin_user, db, user_factory, session_factory):
    curator = user_factory(vk_id=960041, name="Curator", role_name="куратор")
    student = user_factory(vk_id=960042, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.commit()
    cycle = _mk_cycle(db, student.id, closed=True)
    final = _mk_final_work(db, student.id, cycle.id, score=80)
    _mk_staff_message(db, final.id, curator_id=curator.id, sender_role="curator", text="ОС")
    cycle.revision_requested_at = datetime.now(timezone.utc)
    db.commit()

    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    resp = client.post(f"/cabinet/feedback/{cycle.id}/revision-done",
                       headers={"Accept": "application/json"})
    assert resp.status_code == 200
    db.refresh(cycle)
    # requested_at сохраняется как история; «правка завершена» = revision_done_at
    assert cycle.revision_requested_at is not None
    assert cycle.revision_done_at is not None
    assert not cycle.is_on_revision
    assert cycle.closed_at is not None  # остаётся закрытым


def test_returned_cycle_appears_in_curator_open_list(client, db, user_factory, session_factory):
    """Возвращённый (закрытый+флаг) цикл виден в рабочем списке куратора с бейджем."""
    curator = user_factory(vk_id=960051, name="Curator", role_name="куратор")
    student = user_factory(vk_id=960052, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.commit()
    cycle = _mk_cycle(db, student.id, subject="Рисунок", closed=True)
    final = _mk_final_work(db, student.id, cycle.id, score=80)
    _mk_staff_message(db, final.id, curator_id=curator.id, sender_role="curator", text="ОС")
    cycle.revision_requested_at = datetime.now(timezone.utc)
    db.commit()

    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/cabinet/staff/cycles")
    assert resp.status_code == 200
    assert resp.text.count('class="cyc-row"') == 1
    assert "На изменении" in resp.text


def test_dialog_renders_revision_ui_for_curator(client, admin_user, db, user_factory, session_factory):
    """Страница диалога флагнутого цикла рендерится: бейдж, баннер, inline-правка своего сообщения."""
    curator = user_factory(vk_id=960061, name="Curator", role_name="куратор")
    student = user_factory(vk_id=960062, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.commit()
    cycle = _mk_cycle(db, student.id, closed=True)
    final = _mk_final_work(db, student.id, cycle.id, score=80)
    _mk_staff_message(db, final.id, curator_id=curator.id, sender_role="curator", text="ОС")
    cycle.revision_requested_at = datetime.now(timezone.utc)
    db.commit()

    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    resp = client.get(f"/cabinet/curator/feedback/{cycle.id}")
    assert resp.status_code == 200
    assert "НА ИЗМЕНЕНИИ" in resp.text
    assert "✎ Изменить" in resp.text
    assert "Завершить правку" in resp.text


@pytest.mark.parametrize("closed", [True, False])
def test_dialog_renders_return_button_for_superadmin(client, admin_user, db, user_factory, session_factory, closed):
    """SA видит кнопку «Вернуть куратору» на любом цикле с ОС — открытом и закрытом."""
    curator = user_factory(vk_id=960071 + (1 if closed else 0), name="Curator", role_name="куратор")
    student = user_factory(vk_id=960073 + (1 if closed else 0), name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.commit()
    cycle = _mk_cycle(db, student.id, closed=closed)
    final = _mk_final_work(db, student.id, cycle.id, score=80)
    _mk_staff_message(db, final.id, curator_id=curator.id, sender_role="curator", text="ОС")

    sess = session_factory(admin_user)
    client.cookies.set("session_id", sess.id)
    resp = client.get(f"/cabinet/superadmin/feedback/{cycle.id}")
    assert resp.status_code == 200
    assert "Вернуть куратору на изменение" in resp.text
