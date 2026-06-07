"""Тесты редизайна 2026-05-23: диалог обратной связи + закрытие цикла."""
from datetime import date, datetime, timezone

import pytest

from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.exam_cycle import close_cycle_if_scored, has_open_cycles


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


def _mk_final_work(db, user_id, cycle_id, *, score=None, attempt=1):
    w = Work(
        user_id=user_id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="05", year=2026,
        filename=f"final-{attempt}.jpg",
        subject="Drawing",
        status="success",
        is_final=True,
        cycle_id=cycle_id,
        attempt_number=attempt,
        score=score,
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


# ── Service layer: close_cycle_if_scored ─────────────────────────────────────

def test_close_cycle_scored_final_mock_closes_cycle(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id, score=82)
    assert close_cycle_if_scored(db, work) is True
    db.refresh(cycle)
    assert cycle.closed_at is not None


def test_close_cycle_unscored_does_nothing(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id, score=None)
    assert close_cycle_if_scored(db, work) is False
    db.refresh(cycle)
    assert cycle.closed_at is None


def test_close_cycle_already_closed_idempotent(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    original_closed_at = cycle.closed_at
    work = _mk_final_work(db, regular_user.id, cycle.id, score=70)
    assert close_cycle_if_scored(db, work) is False
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
    assert close_cycle_if_scored(db, w) is False
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
    assert "Что усилить:\nТон в тенях" in text
    assert "Рекомендации:\nПоработать над краями" in text


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


def test_message_in_closed_cycle_forbidden(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    work = _mk_final_work(db, regular_user.id, cycle.id, score=80)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "should fail"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 403


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


def test_staff_cycles_page_shows_close_score_badge(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    _mk_final_work(db, regular_user.id, cycle.id, score=88)

    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.get("/cabinet/staff/cycles")

    assert resp.status_code == 200
    assert "88 / 100" in resp.text
    assert "cycle-pill score score-green" in resp.text


def test_cycle_page_shows_close_score_badge_for_closed_cycle(auth_client, db):
    client, user = auth_client
    cycle = _mk_cycle(db, user.id, closed=True)
    _mk_final_work(db, user.id, cycle.id, score=88)

    resp = client.get("/cabinet/cycle")

    assert resp.status_code == 200
    assert "88 / 100" in resp.text
    assert "fb-score-badge score-green" in resp.text
