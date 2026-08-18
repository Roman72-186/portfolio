"""End-to-end behavioral tests for the student role.

Covers the full student journey:
  1. Profile completion flow
  2. Portfolio upload (before → finish-before → after)
  3. Dashboard (home, scores)
  4. Gallery and history
"""
from decimal import Decimal
from datetime import datetime, timezone, date, timedelta


def _auth(client, user_factory, session_factory, **user_kwargs):
    """Helper: create a user, attach a session cookie, return (client, user)."""
    user = user_factory(**user_kwargs)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    return client, user


# ---------------------------------------------------------------------------
# 1. Profile completion flow
# ---------------------------------------------------------------------------

def test_incomplete_profile_redirects_to_profile_form(client, user_factory, session_factory):
    """GET /cabinet/student → redirect to /cabinet/profile when profile_completed=False."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_101, profile_completed=False)
    resp = client.get("/cabinet/student", follow_redirects=False)
    assert resp.status_code == 302
    assert "/cabinet/profile" in resp.headers["location"]


def test_profile_form_returns_200_when_incomplete(client, user_factory, session_factory):
    """GET /cabinet/profile returns 200 with tariff options visible."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_102, profile_completed=False)
    resp = client.get("/cabinet/profile")
    assert resp.status_code == 200
    assert "Максимум" in resp.text
    assert "Уверенный" in resp.text


def test_profile_form_redirects_when_already_complete(auth_client):
    """GET /cabinet/profile redirects to dashboard if already complete."""
    client, _ = auth_client
    resp = client.get("/cabinet/profile", follow_redirects=False)
    assert resp.status_code == 302
    assert "/cabinet/learning" in resp.headers["location"]


def test_profile_form_shows_step_indicator(client, user_factory, session_factory):
    """GET /cabinet/profile shows a 5-step visual indicator on the long form."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_107, profile_completed=False)
    resp = client.get("/cabinet/profile")
    assert resp.status_code == 200
    assert resp.text.count('class="form-step-header"') == 5
    assert "Шаг 5 из 5" in resp.text


def test_profile_post_valid_data_sets_profile_completed(client, db, user_factory, session_factory):
    """Valid POST /cabinet/profile → profile_completed=True, name/tariff saved."""
    from app.models.user import User
    client, user = _auth(client, user_factory, session_factory,
                         vk_id=100_103, profile_completed=False)
    resp = client.post("/cabinet/profile", data={
        "first_name": "Анна",
        "last_name":  "Смирнова",
        "phone":      "+79001112233",
        "parent_phone": "+79002223344",
        "tariff":     "Уверенный",
        "tg_username": "anna_art",
        "enrollment_month": "9",
        "enrollment_year": "2024",
        "university_year": "2025",
        "about": "Хочу поступить в Строгановку",
        "course_periods": "10-14 июня",
        "lessons_count": "8",
    }, follow_redirects=False)
    assert resp.status_code == 302
    db.expire_all()
    db_user = db.query(User).filter(User.id == user.id).first()
    assert db_user.profile_completed is True
    assert db_user.name == "Анна Смирнова"
    assert db_user.tariff == "УВЕРЕННЫЙ"  # normalized to UPPER on save
    assert db_user.enrollment_year == 2024


def test_profile_post_empty_form_shows_all_required_errors(client, user_factory, session_factory):
    """Whitespace-only fields (stripped to empty) return all required-field errors."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_104, profile_completed=False)
    # FastAPI (Pydantic v2) rejects truly empty strings for required Form fields.
    # Sending single space satisfies FastAPI but gets stripped to "" by the handler.
    resp = client.post("/cabinet/profile", data={
        "first_name": " ", "last_name": " ", "phone": " ", "parent_phone": " ",
        "tariff": "Уверенный", "tg_username": " ",
        "enrollment_month": " ", "enrollment_year": " ", "about": " ",
    })
    assert resp.status_code == 200
    for fragment in ("Введите имя", "Введите фамилию", "Введите номер телефона",
                     "Укажите ник в Telegram", "Укажите год поступления",
                     "Укажите месяц присоединения"):
        assert fragment in resp.text, f"Expected error: {fragment!r}"


def test_profile_post_non_integer_year_shows_error(client, user_factory, session_factory):
    """Letters in enrollment_year → 'числом' error."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_105, profile_completed=False)
    resp = client.post("/cabinet/profile", data={
        "first_name": "Иван", "last_name": "П", "phone": "+7", "parent_phone": "+7",
        "tariff": "Уверенный", "tg_username": "iv",
        "enrollment_month": "9", "enrollment_year": "abc", "about": "X",
    })
    assert resp.status_code == 200
    assert "числом" in resp.text


def test_profile_post_invalid_month_shows_error(client, user_factory, session_factory):
    """Out-of-range enrollment_month (13) → month error."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_106, profile_completed=False)
    resp = client.post("/cabinet/profile", data={
        "first_name": "Иван", "last_name": "П", "phone": "+7", "parent_phone": "+7",
        "tariff": "Уверенный", "tg_username": "iv",
        "enrollment_month": "13", "enrollment_year": "2024", "about": "X",
    })
    assert resp.status_code == 200
    assert "месяц" in resp.text.lower()


# ---------------------------------------------------------------------------
# 2. Dashboard
# ---------------------------------------------------------------------------

def test_dashboard_returns_200(auth_client):
    """GET /cabinet/student returns 200 for a student with complete profile."""
    client, _ = auth_client
    resp = client.get("/cabinet/student")
    assert resp.status_code == 200


def test_dashboard_shows_portfolio_cta_when_not_completed(client, user_factory, session_factory):
    """New student (portfolio_do_completed=False) sees the 'upload before photo' CTA."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_108, portfolio_do_completed=False)
    resp = client.get("/cabinet/student")
    assert resp.status_code == 200
    assert "Загрузите работы" in resp.text
    assert 'href="/upload"' in resp.text


def test_dashboard_hides_portfolio_cta_when_completed(auth_client):
    """Student with portfolio_do_completed=True (the fixture default) doesn't see the CTA."""
    client, _ = auth_client
    resp = client.get("/cabinet/student")
    assert resp.status_code == 200
    assert "Загрузите работы" not in resp.text


def test_dashboard_shows_mock_count_and_avg(auth_client, db):
    """Dashboard shows correct mock exam count and average score."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    client, user = auth_client
    for score in (80, 90):
        db.add(Work(
            user_id=user.id, work_type=WORK_TYPE_MOCK_EXAM,
            month="апрель", year=2026, filename="e.jpg",
            subject="Рисунок", score=Decimal(str(score)), status="success",
        ))
    db.commit()
    resp = client.get("/cabinet/student")
    assert resp.status_code == 200
    assert "85" in resp.text   # avg(80, 90)


def test_dashboard_tariff_history_from_upload_log(auth_client, db):
    """Tariff history is populated from UploadLog table."""
    from app.models.upload_log import UploadLog
    client, user = auth_client
    db.add(UploadLog(
        user_id=user.id, student_name=user.name, tariff=user.tariff,
        month="январь", photo_type="before", photo_count=2, status="success",
    ))
    db.commit()
    resp = client.get("/cabinet/student")
    assert resp.status_code == 200


def test_dashboard_shows_upload_button_when_portfolio_after_is_open(auth_client, db):
    """Portfolio tab shows /upload CTA when portfolio upload window is open."""
    from app.constants import FEATURE_PORTFOLIO_UPLOAD
    from app.models.feature_period import FeaturePeriod
    from app.models.user import User

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.add(FeaturePeriod(
        feature=FEATURE_PORTFOLIO_UPLOAD,
        start_date=date.today() - timedelta(days=1),
        end_date=date.today() + timedelta(days=7),
        is_active=True,
        created_by_id=user.id,
    ))
    db.commit()

    from app.services.feature_periods import invalidate_feature_cache
    invalidate_feature_cache(FEATURE_PORTFOLIO_UPLOAD)

    resp = client.get("/cabinet/portfolio")
    assert resp.status_code == 200
    assert 'href="/upload?section=after"' in resp.text
    assert "Загрузить фото" in resp.text


def test_portfolio_renders_before_after_month_blocks(auth_client, db):
    """До/После рендерятся server-side раскрывающимися блоками по месяцам
    (единый вид со staff-панелью), а не старым пикером-помесячником."""
    from app.models.work import Work, WORK_TYPE_BEFORE
    from app.models.user import User

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.add_all([
        Work(user_id=user.id, work_type=WORK_TYPE_BEFORE, month="январь", year=2026, filename="before-1.jpg", s3_url="https://s3.example/before-1.jpg", status="success"),
        Work(user_id=user.id, work_type=WORK_TYPE_BEFORE, month="февраль", year=2026, filename="before-2.jpg", s3_url="https://s3.example/before-2.jpg", status="success"),
    ])
    db.commit()

    resp = client.get("/cabinet/portfolio")
    assert resp.status_code == 200
    assert "pf-mblock" in resp.text          # блоки по месяцам
    assert "pfToggleMonth" in resp.text       # тоггл раскрытия
    assert "before-1.jpg" in resp.text
    assert "before-2.jpg" in resp.text
    assert 'id="portfolio-before-root"' not in resp.text  # старый пикер убран


# ---------------------------------------------------------------------------
# 3. Upload mode switching
# ---------------------------------------------------------------------------

def test_upload_shows_before_mode_by_default(auth_client, db):
    """GET /upload shows 'before' mode when portfolio_do_completed=False (default)."""
    from app.models.user import User
    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": False})
    db.commit()
    resp = client.get("/upload")
    assert resp.status_code == 200
    assert "before" in resp.text or "До" in resp.text


def test_upload_shows_after_mode_when_before_done(auth_client, db):
    """GET /upload shows 'after' mode when portfolio_do_completed=True."""
    from app.models.user import User
    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.commit()
    resp = client.get("/upload")
    assert resp.status_code == 200
    assert "after" in resp.text or "После" in resp.text


def test_upload_explicit_before_mode_available_after_completion(auth_client, db):
    """GET /upload?section=before still opens the BEFORE uploader after completion."""
    from app.models.user import User

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.commit()

    resp = client.get("/upload?section=before")
    assert resp.status_code == 200
    assert "Раздел «До»" in resp.text


def test_finish_before_without_uploads_shows_error(auth_client, db):
    """POST /upload/finish-before with no before works returns an error message."""
    from app.models.user import User
    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": False})
    db.commit()
    resp = client.post("/upload/finish-before")
    assert resp.status_code == 200
    assert "хотя бы одно" in resp.text


def test_finish_before_with_works_sets_flag_and_redirects(auth_client, db):
    """POST /upload/finish-before with a before work → portfolio_do_completed=True."""
    from app.models.user import User
    from app.models.work import Work, WORK_TYPE_BEFORE
    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": False})
    db.add(Work(
        user_id=user.id, work_type=WORK_TYPE_BEFORE,
        month="январь", year=2026, filename="before.jpg", status="success",
    ))
    db.commit()
    resp = client.post("/upload/finish-before", follow_redirects=False)
    assert resp.status_code == 302
    db.expire_all()
    updated = db.query(User).filter(User.id == user.id).first()
    assert updated.portfolio_do_completed is True


# ---------------------------------------------------------------------------
# 4. Scores
# ---------------------------------------------------------------------------

# /cabinet/scores removed in 2026-05-23 redesign. Scores visible in portfolio
# and in the Probnik tab (/cabinet/cycle). Old scores tests dropped; one
# regression check for the new two-tab page is enough here.


def test_cycle_page_returns_200_empty(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/cycle")
    assert resp.status_code == 200


def test_cycle_page_shows_closed_mock_cycle(auth_client, db):
    from app.models.exam_cycle import ExamCycle
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    client, user = auth_client
    cycle = ExamCycle(
        user_id=user.id,
        subject="Рисунок",
        started_at=date(2026, 1, 10),
        closed_at=datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc),
    )
    db.add(cycle)
    db.flush()
    db.add(Work(
        user_id=user.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="01", year=2026,
        filename="a_mock_only.jpg",
        s3_url="https://s3.example/a_mock_only.jpg",
        subject="Рисунок",
        score=Decimal("75"),
        status="success",
        is_final=True,
        cycle_id=cycle.id,
    ))
    db.commit()
    resp = client.get("/cabinet/cycle")
    assert resp.status_code == 200
    assert "Рисунок" in resp.text
    assert "75 / 100" in resp.text


# 5. Gallery and history
# ---------------------------------------------------------------------------

def test_portfolio_page_does_not_render_gallery_button(auth_client):
    """Student portfolio page should not show the separate gallery link."""
    client, _ = auth_client

    resp = client.get("/cabinet/portfolio")

    assert resp.status_code == 200
    assert 'href="/cabinet/gallery"' not in resp.text


def test_gallery_returns_200(auth_client):
    """GET /cabinet/gallery returns 200."""
    client, _ = auth_client
    resp = client.get("/cabinet/gallery")
    assert resp.status_code == 200


def test_gallery_shows_albums_grouped_by_month(auth_client, db):
    """Albums are grouped by month from the student's Work records."""
    from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER
    from datetime import datetime, timezone
    client, user = auth_client
    for work_type in (WORK_TYPE_BEFORE, WORK_TYPE_AFTER):
        db.add(Work(user_id=user.id, work_type=work_type, month="март", year=2026,
                    filename=f"{work_type}.jpg", tariff=user.tariff, status="success",
                    created_at=datetime.now(timezone.utc)))
    db.commit()
    resp = client.get("/cabinet/gallery")
    assert resp.status_code == 200
    # Template renders the month label via `capitalize`: "март" -> "Март".
    assert "Март" in resp.text


def test_gallery_thumb_returns_404_for_another_users_file(auth_client, db, user_factory, session_factory):
    """Thumbnail endpoint returns 404 when file belongs to a different user (IDOR check)."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    client, _ = auth_client
    other = user_factory(vk_id=999_888, name="Other Student")
    db.add(Work(user_id=other.id, work_type=WORK_TYPE_MOCK_EXAM,
                month="апрель", year=2026, filename="secret.jpg",
                drive_file_id="drive_secret_abc", status="success"))
    db.commit()
    from app.services import drive
    drive._file_index[(other.vk_id, "drive_secret_abc")] = {
        "thumbnail_url": "https://drive.example/secret-thumb"
    }
    resp = client.get("/cabinet/gallery/thumb/drive_secret_abc")
    assert resp.status_code == 404
    drive._file_index.clear()


def test_history_returns_200(auth_client):
    """GET /cabinet/history returns 200."""
    client, _ = auth_client
    resp = client.get("/cabinet/history")
    assert resp.status_code == 200


def test_history_shows_correct_total_photo_count(auth_client, db):
    """History page total_photos equals sum of all successful upload photo_counts."""
    from app.models.upload_log import UploadLog
    client, user = auth_client
    db.add(UploadLog(user_id=user.id, student_name=user.name, tariff=user.tariff,
                     month="январь", photo_type="before", photo_count=4, status="success"))
    db.add(UploadLog(user_id=user.id, student_name=user.name, tariff=user.tariff,
                     month="февраль", photo_type="after", photo_count=3, status="success"))
    db.commit()
    resp = client.get("/cabinet/history")
    assert resp.status_code == 200
    assert "7" in resp.text   # total_photos = 4 + 3
