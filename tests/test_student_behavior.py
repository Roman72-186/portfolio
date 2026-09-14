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
    """GET /cabinet/profile returns 200 with tariff options visible.

    Список сузился до действующей линейки (владелец 08.09.2026: «новый учебный
    год, старых учеников поместили в архив и забыли»), поэтому проверяем
    «Уверенный максимум», а не прежний «Максимум». Состав линейки держит
    `tests/test_tariffs_precourse.py`.
    """
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_102, profile_completed=False)
    resp = client.get("/cabinet/profile")
    assert resp.status_code == 200
    assert "Уверенный максимум" in resp.text
    assert "Уверенный" in resp.text


def test_profile_form_shows_hint_component(client, user_factory, session_factory):
    """Анкета подключает пилотные подсказки у часового пояса и ссылки ВК.

    Пилот компонента `components/hint.html` (см. `docs/component-map.md`) —
    ровно две подсказки на этом экране: у СДЭК уже есть инлайн-примечание,
    дублировать его всплывашкой не стали.
    """
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_105, profile_completed=False)
    resp = client.get("/cabinet/profile")
    assert resp.status_code == 200
    assert resp.text.count('class="hint-wrap"') == 2
    assert '<script src="/static/js/hint.js?v=' in resp.text


def test_profile_form_redirects_when_already_complete(auth_client):
    """GET /cabinet/profile → экран правки контактов, если анкета заполнена."""
    client, _ = auth_client
    resp = client.get("/cabinet/profile", follow_redirects=False)
    assert resp.status_code == 302
    assert "/cabinet/personal/contacts" in resp.headers["location"]


def test_profile_post_ignored_when_already_complete(auth_client, db):
    """Повторный POST анкеты не переписывает ФИО и тариф."""
    from app.models.user import User
    client, user = auth_client
    user.first_name = "Анна"
    user.last_name = "Смирнова"
    user.name = "Анна Смирнова"
    db.add(user)
    db.commit()

    resp = client.post("/cabinet/profile", data={
        "first_name": "Взломщик",
        "last_name": "Подменённый",
        "phone": "+79001112233",
        "parent_phone": "+79002223344",
        "tariff": "Максимум",
        "tg_username": "anna_art",
        "university_year": "2025",
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert "/cabinet/personal/contacts" in resp.headers["location"]

    db.expire_all()
    saved = db.query(User).filter(User.id == user.id).first()
    assert saved.name == "Анна Смирнова"
    assert saved.tariff == "УВЕРЕННЫЙ"


def test_profile_form_shows_step_indicator(client, user_factory, session_factory):
    """GET /cabinet/profile shows a 4-step visual indicator on the long form."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_107, profile_completed=False)
    resp = client.get("/cabinet/profile")
    assert resp.status_code == 200
    assert resp.text.count('class="prf-section-title"') == 4
    assert "Шаг 4 из 4" in resp.text


def test_profile_post_valid_data_sets_profile_completed(client, db, user_factory, session_factory):
    """Valid POST /cabinet/profile → profile_completed=True, name/tariff saved."""
    from app.models.user import User
    client, user = _auth(client, user_factory, session_factory,
                         vk_id=100_103, profile_completed=False)
    resp = client.post("/cabinet/profile", data={
        "first_name": "Анна",
        "last_name":  "Смирнова",
        "birth_date": "2010-05-20",
        "city": "Москва",
        "timezone": "0",
        "phone":      "+79001112233",
        "parent_phone": "+79002223344",
        "parent_name": "Ольга Викторовна",
        "vk_profile_url": "vk.com/anna_smirnova",
        "sdek_address": "Москва, ул. Ленина 10, ПВЗ Строгино",
        "email": "anna@example.com",
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
    assert db_user.birth_date.isoformat() == "2010-05-20"
    assert db_user.city == "Москва"
    assert db_user.timezone == "0"
    assert db_user.parent_name == "Ольга Викторовна"
    assert db_user.vk_profile_url == "https://vk.com/anna_smirnova"
    assert db_user.sdek_address == "Москва, ул. Ленина 10, ПВЗ Строгино"
    assert db_user.email == "anna@example.com"
    # Месяц и год присоединения ставит сервер моментом заполнения анкеты,
    # по московскому времени и первым числом месяца — как прежде писала форма.
    from app.services.tz import now_msk
    moment = now_msk()
    assert db_user.enrollment_year == moment.year
    assert db_user.enrolled_at is not None
    assert (db_user.enrolled_at.year, db_user.enrolled_at.month, db_user.enrolled_at.day) ==         (moment.year, moment.month, 1)


def test_profile_post_enrollment_date_shown_on_contacts(client, user_factory, session_factory):
    """Проставленная сама дата присоединения доходит до экрана «Контактные данные».

    Строку там собирают из двух разных колонок — месяц из `enrolled_at`,
    год из `enrollment_year` (`cabinet_personal.py`), поэтому сквозная
    проверка: заполнили анкету — увидели «Месяц Год», а не «Не указано».
    """
    from app.constants import MONTHS
    from app.services.tz import now_msk
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_109, profile_completed=False)
    resp = client.post("/cabinet/profile", data={
        "first_name": "Пётр", "last_name": "Иванов",
        "birth_date": "2009-03-14", "city": "Казань", "timezone": "0",
        "phone": "+79001112233", "parent_phone": "+79002223344",
        "parent_name": "Мария Петровна",
        "vk_profile_url": "vk.com/petr_ivanov",
        "sdek_address": "Казань, ул. Баумана 1, ПВЗ Центр",
        "email": "petr@example.com", "tariff": "Уверенный",
        "tg_username": "petr_art", "university_year": "2025",
        "about": "Хочу в архитектурный",
    }, follow_redirects=False)
    assert resp.status_code == 302

    moment = now_msk()
    page = client.get("/cabinet/personal/contacts")
    assert page.status_code == 200
    expected = f"{MONTHS[moment.month - 1].capitalize()} {moment.year}"
    assert expected in page.text


def test_profile_post_accepts_adult_birth_year(client, db, user_factory, session_factory):
    """Ученик старше тридцати сохраняет анкету: порог года рождения — 1960.

    До 13.09.2026 в коде стоял 1995, и такая анкета падала с текстом
    «Проверь дату рождения», хотя дата была верной.
    """
    from app.models.user import User
    client, user = _auth(client, user_factory, session_factory,
                         vk_id=100_111, profile_completed=False)
    resp = client.post("/cabinet/profile", data={
        "first_name": "Игорь", "last_name": "Петров",
        "birth_date": "1988-04-02", "city": "Пермь", "timezone": "2",
        "phone": "+79001112233", "parent_phone": "+79002223344",
        "parent_name": "Нина Сергеевна",
        "vk_profile_url": "vk.com/igor_petrov",
        "sdek_address": "Пермь, Ленина 5, ПВЗ",
        "email": "igor@example.com", "tariff": "Я сам",
        "tg_username": "igor_art", "university_year": "2027",
    }, follow_redirects=False)
    assert resp.status_code == 302
    db.expire_all()
    assert db.query(User).filter(User.id == user.id).first().birth_date.isoformat() == "1988-04-02"


def test_profile_post_rejects_birth_year_below_floor(client, user_factory, session_factory):
    """Год ниже порога — явная опечатка, форма возвращается с ошибкой."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_112, profile_completed=False)
    resp = client.post("/cabinet/profile", data={
        "first_name": "Игорь", "last_name": "Петров",
        "birth_date": "1905-04-02", "city": "Пермь", "timezone": "2",
        "phone": "+79001112233", "parent_phone": "+79002223344",
        "parent_name": "Нина Сергеевна",
        "vk_profile_url": "vk.com/igor_petrov",
        "sdek_address": "Пермь, Ленина 5, ПВЗ",
        "email": "igor@example.com", "tariff": "Я сам",
        "tg_username": "igor_art", "university_year": "2027",
    })
    assert resp.status_code == 200
    assert "Проверь дату рождения" in resp.text


def test_profile_form_limits_birth_date_field(client, user_factory, session_factory):
    """Поле даты ограничено теми же границами, что проверяет сервер."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_113, profile_completed=False)
    resp = client.get("/cabinet/profile")
    assert resp.status_code == 200
    from app.services.tz import today_msk
    assert 'min="1960-01-01"' in resp.text
    # Верхняя граница — сегодня по Москве: контейнер живёт в UTC, и date.today()
    # ночью отняло бы у именинника его собственный день рождения.
    assert f'max="{today_msk().isoformat()}"' in resp.text


def test_vk_profile_url_accepts_both_domains():
    """Ссылка с vk.ru и m.vk.ru принимается и приводится к каноническому vk.com."""
    from app.api.cabinet_student import VK_RE, normalize_vk_profile_url
    accepted = [
        "https://vk.com/al_vetv",
        "vk.com/al_vetv",
        "https://vk.ru/al_vetv",          # новый российский домен ВК
        "https://m.vk.ru/al_vetv",        # так копирует мобильное приложение
        "m.vk.ru/al_vetv/",
        "VK.RU/al_vetv",
        "https://vk.ru/al_vetv?from=groups",
    ]
    for raw in accepted:
        assert VK_RE.match(raw), raw
        assert normalize_vk_profile_url(raw) == "https://vk.com/al_vetv", raw

    for raw in ["https://ok.ru/al_vetv", "https://vk.xx/al_vetv", "vk.ru/"]:
        assert not VK_RE.match(raw), raw


def test_profile_post_empty_form_shows_all_required_errors(client, user_factory, session_factory):
    """Whitespace-only fields (stripped to empty) return all required-field errors."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_104, profile_completed=False)
    # FastAPI (Pydantic v2) rejects truly empty strings for required Form fields.
    # Sending single space satisfies FastAPI but gets stripped to "" by the handler.
    resp = client.post("/cabinet/profile", data={
        "first_name": " ", "last_name": " ", "phone": " ", "parent_phone": " ",
        "tariff": "Уверенный", "tg_username": " ",
        "birth_date": " ", "city": " ", "timezone": " ",
        "parent_name": " ", "vk_profile_url": " ", "sdek_address": " ", "email": " ",
        "about": " ",
    })
    assert resp.status_code == 200
    for fragment in ("Введи имя", "Введи фамилию", "Введи номер телефона",
                     "Укажи ник в Telegram", "Укажи год поступления",
                     "Укажи дату рождения",
                     "Укажи город", "Укажи часовой пояс",
                     "Введи имя и отчество родителя", "Укажи ссылку на ВКонтакте",
                     "Укажи ближайший адрес СДЭК", "Укажи электронную почту"):
        assert fragment in resp.text, f"Expected error: {fragment!r}"


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
    assert "Загрузи работы" in resp.text
    assert 'href="/upload"' in resp.text


def test_dashboard_hides_portfolio_cta_when_completed(auth_client):
    """Student with portfolio_do_completed=True (the fixture default) doesn't see the CTA."""
    client, _ = auth_client
    resp = client.get("/cabinet/student")
    assert resp.status_code == 200
    assert "Загрузи работы" not in resp.text


def test_bottom_nav_shows_portfolio_gate_when_not_completed(client, user_factory, session_factory):
    """Пока portfolio_do_completed=False, меню несёт гейт-поп-ап и блокирует
    клик по всем пунктам, кроме «Актуального образовательного пространства»."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_109, portfolio_do_completed=False)
    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert 'id="portfolioGateModal"' in resp.text
    assert 'data-nav-key="learning"' in resp.text
    assert 'href="/upload"' in resp.text


def test_bottom_nav_hides_portfolio_gate_when_completed(auth_client):
    """Student with portfolio_do_completed=True (the fixture default) doesn't get the gate."""
    client, _ = auth_client
    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert 'id="portfolioGateModal"' not in resp.text


# ---------------------------------------------------------------------------
# 2b. Server-side enforcement of the "Portfolio До" gate (not just the popup)
# ---------------------------------------------------------------------------

def test_portfolio_gate_blocks_direct_url_to_locked_pages(client, user_factory, session_factory):
    """Прямой переход по ссылке (не клик по меню) на закрытый раздел тоже
    должен упираться в гейт — редиректом на ленту с открытым поп-апом."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_110, portfolio_do_completed=False)
    for path in ("/cabinet/tracker", "/cabinet/portfolio", "/cabinet/feedback/", "/3dlab"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 302, path
        assert resp.headers["location"] == "/cabinet/learning?locked=portfolio", path


def test_portfolio_gate_allows_direct_url_when_completed(auth_client):
    """Student with portfolio_do_completed=True (the fixture default) isn't gated."""
    client, _ = auth_client
    for path in ("/cabinet/tracker", "/cabinet/portfolio"):
        resp = client.get(path)
        assert resp.status_code == 200, path


def test_portfolio_gate_defers_to_profile_setup(client, user_factory, session_factory):
    """Пока не заполнена самая первая анкета, приоритетнее её редирект —
    гейт портфолио тут ни при чём (см. `user.profile_completed` в условии)."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_111, profile_completed=False, portfolio_do_completed=False)
    resp = client.get("/cabinet/tracker", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/profile"


def test_locked_query_param_opens_the_gate_modal(client, user_factory, session_factory):
    """`?locked=portfolio` (куда редиректит гейт) рисует поп-ап уже открытым."""
    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_112, portfolio_do_completed=False)
    resp = client.get("/cabinet/learning?locked=portfolio")
    assert resp.status_code == 200
    assert 'class="gate-modal open"' in resp.text


def test_portfolio_gate_does_not_block_tracker_block_endpoints(client, db, user_factory, session_factory):
    """`/cabinet/tracker` в блок-листе только точным путём: вложенные
    эндпоинты блоков (использует и открытая лента `/cabinet/learning`,
    общий рендерер task-blocks-render.js) должны продолжать работать."""
    from app.services.tracker import create_task
    from app.models.task_block import TaskBlock, BLOCK_TEXT

    staff = user_factory(vk_id=550_310, name="Стафф", is_admin=True, role_name="админ")
    task = create_task(db, title="Материал", user_id=staff.id, kind="material", assign_to_all=True)
    task.is_published = True
    db.add(TaskBlock(task_id=task.id, sort_order=0, block_type=BLOCK_TEXT, body="Текст"))
    db.commit()
    db.refresh(task)

    client, _ = _auth(client, user_factory, session_factory,
                      vk_id=100_113, portfolio_do_completed=False)
    resp = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks")
    assert resp.status_code == 200
    assert resp.json()["blocks"][0]["body"] == "Текст"


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


def test_dashboard_shows_upload_button_without_feature_period(auth_client, db):
    """Кнопка «Загрузить фото» на вкладке Портфолио видна без окна FeaturePeriod.

    Сторожевой тест решения владельца 09.09.2026 («эти триггеры нужно выключить
    сейчас — всё, что было»): доступ к загрузке даёт задание учебной программы,
    глобальное окно больше ни на что не влияет. Раньше тот же тест сначала
    заводил активный FeaturePeriod — без него кнопка пряталась.
    """
    from app.models.user import User

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.commit()

    resp = client.get("/cabinet/portfolio")
    assert resp.status_code == 200
    assert 'href="/upload?section=after"' in resp.text
    assert "Загрузить фото" in resp.text


def test_portfolio_before_renders_flat_without_months(auth_client, db):
    """«До» — одна плоская лента (владелец 09.09.2026: «в До добавляется не по
    месяцам»).

    Проверяем участок разметки между заголовками разделов, а не страницу
    целиком: месячные блоки остаются у «После», и ассерт по всей странице
    прошёл бы, ничего не проверив.
    """
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
    before_section = resp.text[
        resp.text.index(">До обучения<"):resp.text.index(">В процессе обучения<")
    ]
    assert "before-1.jpg" in before_section
    assert "before-2.jpg" in before_section
    assert "pf-mblock" not in before_section      # без раскрывашек по месяцам
    assert "pfToggleMonth" not in before_section
    assert "январь" not in before_section          # и без подписи месяца
    assert 'id="portfolio-before-root"' not in resp.text  # старый пикер убран


def test_portfolio_before_shows_point_a_score_from_db(auth_client, db):
    """Балл точки А читается из базы (владелец 09.09.2026: показать ученику).

    Поле намеренно не входит в user-dict сессии (комментарий у колонок в
    app/models/user.py) — ставит его ГП у чужого ученика, и сбросить чужую
    сессию нечем. Обновляем колонку напрямую в базе, минуя сессию, чтобы
    убедиться, что экран читает актуальное значение, а не устаревшую сессию.
    """
    from app.models.user import User

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({
        "portfolio_do_completed": True,
        "portfolio_before_score": 82,
    })
    db.commit()

    resp = client.get("/cabinet/portfolio")

    assert resp.status_code == 200
    before_section = resp.text[
        resp.text.index(">До обучения<"):resp.text.index(">В процессе обучения<")
    ]
    assert "82 из 100" in before_section


def test_portfolio_before_hides_score_line_when_not_scored(auth_client, db):
    """Пока балла нет — строка не рендерится вовсе, а не «оценка: —»."""
    from app.models.user import User

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.commit()

    resp = client.get("/cabinet/portfolio")

    assert resp.status_code == 200
    before_section = resp.text[
        resp.text.index(">До обучения<"):resp.text.index(">В процессе обучения<")
    ]
    assert "из 100" not in before_section
    assert "оценка" not in before_section.lower()


def test_portfolio_after_keeps_month_blocks(auth_client, db):
    """«После» остаётся помесячным — владелец просил убрать месяцы только у «До»."""
    from app.models.work import Work, WORK_TYPE_AFTER
    from app.models.user import User

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.add(Work(
        user_id=user.id, work_type=WORK_TYPE_AFTER, month="январь", year=2026,
        filename="after-1.jpg", s3_url="https://s3.example/after-1.jpg", status="success",
    ))
    db.commit()

    resp = client.get("/cabinet/portfolio")
    assert resp.status_code == 200
    after_section = resp.text[resp.text.index(">В процессе обучения<"):]
    assert "after-1.jpg" in after_section
    assert "pf-mblock" in after_section
    assert "pfToggleMonth" in after_section
    assert "январь" in after_section


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


def test_portfolio_has_no_separate_mock_exam_tab(auth_client):
    """Отдельная вкладка «Пробные экзамены» снесена на этой странице
    (владелец 14.09.2026) — календарь предметов и showPeers с неё ушли."""
    client, _ = auth_client
    resp = client.get("/cabinet/portfolio")
    assert resp.status_code == 200
    assert "Пробные экзамены" not in resp.text
    assert "showPeers" not in resp.text


def test_mock_exam_final_shows_in_after_group(auth_client, db):
    """Оценённый финал пробника попадает в «В процессе обучения» вместо
    снесённой вкладки «Пробные экзамены» (владелец 14.09.2026: «все сданные
    работы будут попадать в В процессе обучения»)."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    client, user = auth_client
    db.add(Work(
        user_id=user.id, work_type=WORK_TYPE_MOCK_EXAM, month="Январь", year=2026,
        filename="final.jpg", subject="Рисунок", status="success",
        s3_url="https://example.test/final.jpg",
        is_final=True, attempt_number=1, score=70,
    ))
    db.commit()

    resp = client.get("/cabinet/portfolio")

    assert resp.status_code == 200
    assert "https://example.test/final.jpg" in resp.text


def test_ungraded_mock_exam_does_not_show_in_after_group(auth_client, db):
    """Неоценённая попытка (нет score) в «После» не попадает — та же граница,
    что раньше держала вкладку «Пробные экзамены» (только закрытые/оценённые)."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    client, user = auth_client
    db.add(Work(
        user_id=user.id, work_type=WORK_TYPE_MOCK_EXAM, month="Январь", year=2026,
        filename="draft.jpg", subject="Рисунок", status="success",
        s3_url="https://example.test/draft.jpg",
        is_final=False, attempt_number=1, score=None,
    ))
    db.commit()

    resp = client.get("/cabinet/portfolio")

    assert resp.status_code == 200
    assert "https://example.test/draft.jpg" not in resp.text


def test_portfolio_before_upload_button_hidden_once_completed(auth_client):
    """`portfolio_do_completed=True` (дефолт фикстуры) — кнопка «Загрузить
    фото» в разделе «До обучения» больше не нужна, это разовая загрузка."""
    client, _ = auth_client
    resp = client.get("/cabinet/portfolio")
    assert resp.status_code == 200
    assert 'href="/upload?section=before"' not in resp.text
    # Кнопка «После» остаётся — её показывает свой отдельный флаг.
    assert 'href="/upload?section=after"' in resp.text


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
