"""Tests for /cabinet route."""
import re


def test_cabinet_without_auth_redirects(client):
    resp = client.get("/cabinet", follow_redirects=False)
    # HTTPException(401) → redirected to /?error=session_expired
    assert resp.status_code == 302
    assert "session_expired" in resp.headers["location"]


def test_cabinet_with_valid_session_returns_200(auth_client):
    client, user = auth_client
    resp = client.get("/cabinet")
    assert resp.status_code == 200


def test_cabinet_shows_student_name(auth_client, db):
    # /cabinet теперь редиректит ученика на /cabinet/learning (тема недели,
    # без личных данных) — имя/тариф смотрим на /cabinet/tracker, его личном трекере.
    # Показывается имя, заполненное учеником при регистрации (first_name/last_name),
    # а не никнейм из Telegram (user.name) — см. TODO.md.
    client, user = auth_client
    user.first_name = "Анна"
    user.last_name = "Смирнова"
    db.commit()

    resp = client.get("/cabinet/tracker")
    assert "Смирнова Анна" in resp.text
    assert user.name not in resp.text


def test_cabinet_shows_tariff(auth_client):
    client, user = auth_client
    resp = client.get("/cabinet/tracker")
    assert user.tariff in resp.text


def test_student_dashboard_has_mobile_logout_action(auth_client):
    client, _ = auth_client

    resp = client.get("/cabinet/student")

    assert resp.status_code == 200
    assert 'class="mobile-dashboard-logout"' in resp.text
    assert 'method="post" action="/logout"' in resp.text
    assert 'aria-label="Выйти из аккаунта"' in resp.text
    # Требование владельца: в hero-карточке значок без подписи — текст "Выйти"
    # рядом с иконкой убран (десктопный сайдбар с текстом — отдельный элемент,
    # его не трогаем).
    mobile_button = re.search(
        r'aria-label="Выйти из аккаунта">(.*?)</button>', resp.text, re.S
    )
    assert mobile_button is not None
    assert "<span>" not in mobile_button.group(1)


def test_profile_hero_scores_show_dash_without_results(auth_client):
    """Р/К в шапке кабинета — прочерк, пока нет ни одной оценённой пробной работы."""
    client, _ = auth_client
    resp = client.get("/cabinet/tracker")
    assert resp.status_code == 200
    assert '<span class="profile-score-value">-</span>' in resp.text


def test_profile_hero_scores_show_average_when_present(auth_client, db):
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    client, user = auth_client
    db.add(Work(
        user_id=user.id, work_type=WORK_TYPE_MOCK_EXAM, subject="Рисунок",
        month="январь", year=2026, filename="mock.jpg",
        status="success", score=72, tariff=user.tariff,
    ))
    db.commit()

    resp = client.get("/cabinet/tracker")
    assert resp.status_code == 200
    assert '<span class="profile-score-value">72</span>' in resp.text


def test_cabinet_student_shows_mock_exam_empty_state(auth_client):
    """With no mock exams, cabinet/student shows empty-state CTA link."""
    client, _ = auth_client
    resp = client.get("/cabinet/student")
    assert resp.status_code == 200
    assert "пробник" in resp.text.lower()
    assert "/upload/mock-exam" in resp.text


def test_cabinet_student_hides_expired_mock_exam_attempt(auth_client, db):
    """Попытка с проставленным expired_at не должна показываться в виджете
    «Идёт пробник» — иначе зависшая попытка маячила бы на дашборде вечно
    (см. exam_scheduler._run_mock_exam_expiry_check)."""
    from datetime import datetime, timezone
    from app.models.mock_exam_attempt import MockExamAttempt

    client, user = auth_client
    attempt = MockExamAttempt(
        user_id=user.id,
        subject="Рисунок",
        ticket_title="Билет №3 (архивный)",
        expired_at=datetime.now(timezone.utc),
    )
    db.add(attempt)
    db.commit()

    resp = client.get("/cabinet/student")
    assert resp.status_code == 200
    assert "Идёт пробник" not in resp.text
    assert "Билет №3 (архивный)" not in resp.text


def test_cabinet_blocked_user_gets_403(client, db, user_factory, session_factory):
    user = user_factory(is_active=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/cabinet", follow_redirects=False)
    # dependencies.py raises HTTP 403 for blocked users; there is no 403 handler so it passes through
    assert resp.status_code == 403
