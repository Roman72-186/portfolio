"""Aggregation correctness for _load_dashboard_data (Фаза 6, п.4 — не было прямых тестов).

/cabinet/admin-panel рендерит cabinet_staff.html из полутора сотен строк
агрегаций (роли, работы по типам, средний балл, «в этом месяце»). Ни один
существующий тест не фиксирует, что эти числа реально считаются правильно.
"""
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM
from app.services.staff_dashboard import REGISTRATION_STATS_SINCE
from app.services.tz import msk_midnight


def _stat_pair(text: str, value, label: str) -> bool:
    """Дашборд рендерит .stat-tile: сначала label в .stat-tile-head, потом value в .stat-tile-value."""
    pattern = re.compile(
        re.escape(label) + r'[^<]*</div>\s*<div class="stat-tile-value">' + re.escape(str(value)) + r'</div>'
    )
    return pattern.search(text) is not None


def test_dashboard_counts_students_not_staff(admin_client, db, user_factory):
    """Плитка «Учеников» считает только учеников: до 28.09.2026 в неё попадали
    куратор и сам суперадмин, а заблокированными считались сотрудники тоже."""
    client, admin = admin_client
    user_factory(vk_id=200_001, name="Student A", role_name="ученик")
    user_factory(vk_id=200_002, name="Student B", role_name="ученик")
    user_factory(vk_id=200_003, name="Curator A", role_name="куратор")
    user_factory(vk_id=200_004, name="Inactive Student", role_name="ученик", is_active=False)
    user_factory(vk_id=200_005, name="Inactive Curator", role_name="куратор", is_active=False)

    resp = client.get("/cabinet/admin-panel")
    assert resp.status_code == 200
    text = resp.text

    assert _stat_pair(text, 2, "Учеников")
    assert "ещё 1 заблокирован" in text


def test_dashboard_works_by_type_and_this_month_counts(admin_client, db):
    """works_by_type/total_works/works_this_month считают по status=success и месяцу."""
    client, admin = admin_client
    now = datetime.now(timezone.utc)
    last_month = now.replace(day=1) - timedelta(days=1)

    db.add_all([
        Work(user_id=admin.id, work_type=WORK_TYPE_BEFORE, month="май", year=now.year,
             filename="b1.jpg", status="success", created_at=now),
        Work(user_id=admin.id, work_type=WORK_TYPE_BEFORE, month="май", year=now.year,
             filename="b2.jpg", status="success", created_at=now),
        Work(user_id=admin.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="m1.jpg", status="success", created_at=now, score=Decimal("80")),
        Work(user_id=admin.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="m2.jpg", status="success", created_at=now),  # unscored
        # Прошлый месяц — не должен попасть в works_this_month, но должен в total_works.
        Work(user_id=admin.id, work_type=WORK_TYPE_AFTER, month="апрель", year=last_month.year,
             filename="a1.jpg", status="success", created_at=last_month),
        # failed — не должен попасть никуда.
        Work(user_id=admin.id, work_type=WORK_TYPE_BEFORE, month="май", year=now.year,
             filename="failed.jpg", status="failed", created_at=now),
    ])
    db.commit()

    resp = client.get("/cabinet/admin-panel")
    assert resp.status_code == 200
    text = resp.text

    assert "Всего 5 работ" in text  # total_works: 2 before + 2 mock + 1 after (failed исключён)
    assert _stat_pair(text, 4, "Загружено в")  # works_this_month: 2 before + 2 mock, апрель не считается
    assert '<div class="type-val">2</div>' in text  # before
    # avg_score: единственная оценённая работа — 80.
    assert _stat_pair(text, 80, "Средний балл за пробники")


def test_dashboard_unscored_mocks_is_zero_without_active_period(admin_client, db):
    """unscored_mocks не считает работы, если нет активного FeaturePeriod для мок-экзамена.

    Реальных неоценённых пробников в БД — два, но без активного периода
    _load_dashboard_data обязан вернуть 0, а не «все неоценённые за всё время».
    """
    client, admin = admin_client
    now = datetime.now(timezone.utc)
    db.add_all([
        Work(user_id=admin.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="u1.jpg", status="success", created_at=now),
        Work(user_id=admin.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="u2.jpg", status="success", created_at=now),
    ])
    db.commit()

    resp = client.get("/cabinet/admin-panel")
    assert resp.status_code == 200
    assert 'data-attention="mocks"' not in resp.text
    assert 'data-attention="closed"' in resp.text


def _registration_counts(text: str) -> dict[str, int]:
    """Плитки карточки «Регистрации по тарифам» на «Статистике активности»:
    число в .ss-stat-val, подпись тарифа в .ss-stat-lbl."""
    card = text.split("Регистрации по тарифам", 1)[1].split("Список учеников", 1)[0]
    return {
        label: int(value)
        for value, label in re.findall(
            r'<div class="ss-stat-val">(\d+)</div><div class="ss-stat-lbl">([^<]+)</div>',
            card,
        )
    }


def test_registration_tariff_stats_match_for_chief_teacher_and_superadmin(
    client, db, user_factory, session_factory
):
    cutoff = msk_midnight(REGISTRATION_STATS_SINCE)
    students = [
        user_factory(vk_id=820_001, tariff="Я САМ"),
        user_factory(vk_id=820_002, tariff="Я С ВАМИ"),
        user_factory(vk_id=820_003, tariff="УВЕРЕННЫЙ МАКСИМУМ"),
        user_factory(vk_id=820_004, tariff=""),
    ]
    students[0].tg_username = "student_self"
    for student in students:
        student.created_at = cutoff
    chief_teacher = user_factory(
        vk_id=820_010, name="Главный преподаватель", role_name="админ"
    )
    superadmin = user_factory(
        vk_id=820_011, name="Суперадмин", role_name="суперадмин"
    )
    db.commit()

    # Карточка живёт на «Статистике активности» с 25.09.2026 (f9bdbb9): у ГП
    # и суперадмина одна страница и одни числа.
    chief_session = session_factory(chief_teacher)
    client.cookies.set("session_id", chief_session.id)
    chief_response = client.get("/cabinet/superadmin/activity")

    superadmin_session = session_factory(superadmin)
    client.cookies.set("session_id", superadmin_session.id)
    superadmin_response = client.get("/cabinet/superadmin/activity")

    assert chief_response.status_code == 200
    assert superadmin_response.status_code == 200
    assert "Регистрации по тарифам" in chief_response.text
    assert "student_self" in chief_response.text
    chief_counts = _registration_counts(chief_response.text)
    assert chief_counts["Без тарифа"] == 1
    assert sum(chief_counts.values()) == 4
    assert _registration_counts(superadmin_response.text) == chief_counts
    assert 'href="/cabinet/admin/registration-stats.csv?' in chief_response.text
    assert 'href="/cabinet/superadmin/registration-stats.csv?' in superadmin_response.text

    chief_csv = client.get("/cabinet/admin/registration-stats.csv", cookies={"session_id": chief_session.id})
    superadmin_csv = client.get(
        "/cabinet/superadmin/registration-stats.csv",
        cookies={"session_id": superadmin_session.id},
    )
    assert chief_csv.status_code == 200
    assert superadmin_csv.status_code == 200
    assert chief_csv.headers["content-disposition"] == "attachment; filename=registration-stats.csv"
    assert "Имя;Username;Тариф;Дата регистрации" in chief_csv.content.decode("utf-8-sig")
    assert "@student_self" in superadmin_csv.content.decode("utf-8-sig")


# ---------------------------------------------------------------------------
# Разбор дашборда 28.09.2026 (/critique)
# ---------------------------------------------------------------------------

def _open_mock_period(db, created_by_id: int) -> None:
    from app.constants import FEATURE_MOCK_EXAM
    from app.models.feature_period import FeaturePeriod
    from app.services.tz import today_msk

    today = today_msk()
    db.add(FeaturePeriod(
        feature=FEATURE_MOCK_EXAM, title="Пробник", start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=5), created_by_id=created_by_id,
    ))
    db.commit()


def test_average_score_counts_only_mock_exams(admin_client, db):
    """«Средний балл за пробники» раньше брал баллы всех работ подряд."""
    client, admin = admin_client
    now = datetime.now(timezone.utc)
    db.add_all([
        Work(user_id=admin.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="m.jpg", status="success", created_at=now, score=Decimal("60")),
        Work(user_id=admin.id, work_type=WORK_TYPE_BEFORE, month="май", year=now.year,
             filename="b.jpg", status="success", created_at=now, score=Decimal("100")),
    ])
    db.commit()

    text = client.get("/cabinet/admin-panel").text

    assert _stat_pair(text, 60, "Средний балл за пробники")


def test_attention_block_counts_unscored_mocks_with_plural(admin_client, db):
    client, admin = admin_client
    _open_mock_period(db, admin.id)
    now = datetime.now(timezone.utc)
    db.add(Work(user_id=admin.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
                filename="u.jpg", status="success", created_at=now))
    db.commit()

    text = client.get("/cabinet/admin-panel").text

    assert 'data-attention="mocks"' in text
    assert "пробник без оценки" in text


def test_attention_block_says_all_scored_inside_open_period(admin_client, db):
    client, admin = admin_client
    _open_mock_period(db, admin.id)

    text = client.get("/cabinet/admin-panel").text

    assert 'data-attention="none"' in text


def test_dashboard_has_no_portfolio_period_toggle(admin_client):
    """Окно загрузки портфолио с 09.09.2026 ни на что не влияет
    (docs/invariants/portfolio.md) — кнопка «Открыть/Закрыть» была обманкой."""
    client, _ = admin_client

    text = client.get("/cabinet/admin-panel").text

    assert "/cabinet/periods/quick-toggle" not in text
    assert "Периоды сдачи" not in text


def test_irreversible_staff_actions_ask_for_confirmation(admin_client, user_factory):
    client, _ = admin_client
    user_factory(vk_id=200_100, name="Curator Confirm", role_name="куратор")

    text = client.get("/cabinet/superadmin").text

    password_form = text.split('action="/cabinet/superadmin/set-credentials"', 1)[1].split("</form>", 1)[0]
    assert 'data-confirm="Выдать новый пароль' in password_form
    impersonate_form = text.split('action="/cabinet/superadmin/impersonate/', 1)[1].split("</form>", 1)[0]
    assert 'data-confirm="Открыть кабинет' in impersonate_form


def test_chief_teacher_dashboard_hides_superadmin_only_blocks(client, user_factory, session_factory):
    """ГП не видит служебную «Проверку моста», разбивку по ролям и список ГП,
    где ей нечего нажать (владелец 28.09.2026)."""
    chief = user_factory(vk_id=200_200, name="Chief", role_name="админ")
    sess = session_factory(chief)
    client.cookies.set("session_id", sess.id)

    text = client.get("/cabinet/admin-panel").text

    assert "/cabinet/admin/video-bridge-test" not in text
    assert "Пользователи по ролям" not in text
    assert "Главные преподаватели и модераторы" not in text
    assert "Кураторы" in text


def test_ru_plural_forms():
    from app.tmpl import ru_plural

    forms = ("работа", "работы", "работ")
    assert [ru_plural(n, *forms) for n in (0, 1, 2, 5, 11, 12, 21, 22, 25, 101, 111)] == [
        "работ", "работа", "работы", "работ", "работ", "работ",
        "работа", "работы", "работ", "работа", "работ",
    ]
