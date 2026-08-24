"""Календарь учебных программ и экран дня."""

from datetime import date, datetime, timedelta, timezone

from app.models.tracker import ITEM_MOCK_EXAM, ITEM_VIDEO, TrackerTask


PROGRAM = "/cabinet/staff/program"


def _staff_client(client, user_factory, session_factory, *, role_name="админ", vk_id=520_004):
    user = user_factory(
        vk_id=vk_id,
        name="Главный преподаватель",
        is_admin=role_name in ("админ", "суперадмин"),
        is_group_member=False,
        role_name=role_name,
    )
    session = session_factory(user)
    client.cookies.set("session_id", session.id)
    return user


def _freeze_today(monkeypatch, value: date):
    """Заморозить «сегодня» там, где его читает экран."""
    monkeypatch.setattr("app.api.cabinet_program.today_msk", lambda: value)
    monkeypatch.setattr("app.services.program.today_msk", lambda: value)


# ── Доступ ────────────────────────────────────────────────────────────────

def test_student_and_curator_cannot_open_program(
    client, user_factory, session_factory, auth_client
):
    student_client, _ = auth_client
    assert student_client.get(PROGRAM).status_code == 403

    student_client.cookies.clear()
    _staff_client(client, user_factory, session_factory, role_name="куратор", vk_id=520_002)
    assert client.get(PROGRAM).status_code == 403


def test_admin_opens_the_month(client, user_factory, session_factory, monkeypatch):
    _freeze_today(monkeypatch, date(2026, 8, 21))
    _staff_client(client, user_factory, session_factory)

    page = client.get(PROGRAM)

    assert page.status_code == 200
    assert "Учебные программы" in page.text
    assert "Август 2026" in page.text
    assert "/static/css/program.css?v=4" in page.text


# ── Сетка ─────────────────────────────────────────────────────────────────

def test_weekends_are_marked_and_weekdays_are_not(
    client, user_factory, session_factory, monkeypatch
):
    """Тёмные ячейки выходных — то, чем недели отделяются друг от друга."""
    _freeze_today(monkeypatch, date(2026, 8, 21))
    _staff_client(client, user_factory, session_factory)

    page = client.get(f"{PROGRAM}?month=2026-08").text

    assert 'data-dow="6"' in page and 'data-dow="7"' in page
    # 22 августа 2026 — суббота, 24 августа — понедельник.
    assert 'data-dow="6"\n           href="/cabinet/staff/program/2026-08-22"' in page.replace("\r\n", "\n")
    assert 'data-dow="1"\n           href="/cabinet/staff/program/2026-08-24"' in page.replace("\r\n", "\n")


def test_grid_starts_on_monday_of_the_first_week(
    client, user_factory, session_factory, monkeypatch
):
    """Февраль 2026 начинается с воскресенья — сетка стартует с 26 января."""
    _freeze_today(monkeypatch, date(2026, 2, 10))
    _staff_client(client, user_factory, session_factory)

    page = client.get(f"{PROGRAM}?month=2026-02").text

    assert "/cabinet/staff/program/2026-01-26" in page
    assert "/cabinet/staff/program/2026-03-01" in page


def test_month_arrows_cross_the_year(client, user_factory, session_factory, monkeypatch):
    _freeze_today(monkeypatch, date(2026, 12, 15))
    _staff_client(client, user_factory, session_factory)

    page = client.get(f"{PROGRAM}?month=2026-12").text

    assert "?month=2027-01" in page
    assert "?month=2026-11" in page


def test_broken_month_falls_back_to_today(client, user_factory, session_factory, monkeypatch):
    _freeze_today(monkeypatch, date(2026, 8, 21))
    _staff_client(client, user_factory, session_factory)

    assert "Август 2026" in client.get(f"{PROGRAM}?month=сентябрь").text


def test_marks_show_what_stands_in_the_day(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze_today(monkeypatch, date(2026, 8, 21))
    admin = _staff_client(client, user_factory, session_factory)
    db.add_all(
        [
            TrackerTask(
                title="Пробник",
                kind=ITEM_MOCK_EXAM,
                subject="Рисунок",
                due_at=datetime(2026, 8, 24, 8, 45, tzinfo=timezone.utc),
                created_by_id=admin.id,
            ),
            TrackerTask(
                title="Видео недели",
                kind=ITEM_VIDEO,
                due_at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
                created_by_id=admin.id,
            ),
        ]
    )
    db.commit()

    page = client.get(f"{PROGRAM}?month=2026-08").text

    assert "элементов: 1" in page
    assert 'title="Пробник: Рисунок"' in page
    assert 'title="Видеоматериал"' in page


# ── Экран дня ─────────────────────────────────────────────────────────────

def test_future_day_offers_three_tiles(client, user_factory, session_factory, monkeypatch):
    _freeze_today(monkeypatch, date(2026, 8, 21))
    _staff_client(client, user_factory, session_factory)

    page = client.get(f"{PROGRAM}/2026-08-24")

    assert page.status_code == 200
    assert "24 август 2026, понедельник" in page.text
    assert 'data-open-form="mock"' in page.text
    assert 'data-open-form="video"' in page.text
    assert 'data-open-form="homework"' in page.text


def test_past_day_is_read_only(client, db, user_factory, session_factory, monkeypatch):
    """Элемент задним числом открылся бы ученикам мгновенно."""
    _freeze_today(monkeypatch, date(2026, 8, 21))
    admin = _staff_client(client, user_factory, session_factory)
    db.add(
        TrackerTask(
            title="Старый пробник",
            kind=ITEM_MOCK_EXAM,
            due_at=datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc),
            created_by_id=admin.id,
        )
    )
    db.commit()

    page = client.get(f"{PROGRAM}/2026-08-17")

    assert page.status_code == 200
    assert "Старый пробник" in page.text
    # Плиток нет: проверяем разметку, а не строку — селектор с тем же именем
    # встречается в обработчике на странице.
    assert 'class="prg-tile"' not in page.text
    assert 'class="prg-form"' not in page.text
    assert "не менять" in page.text


def test_day_shows_only_its_own_items(client, db, user_factory, session_factory, monkeypatch):
    _freeze_today(monkeypatch, date(2026, 8, 21))
    admin = _staff_client(client, user_factory, session_factory)
    db.add_all(
        [
            TrackerTask(
                title="Понедельник",
                kind=ITEM_VIDEO,
                due_at=datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
                created_by_id=admin.id,
            ),
            TrackerTask(
                title="Вторник",
                kind=ITEM_VIDEO,
                due_at=datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc),
                created_by_id=admin.id,
            ),
        ]
    )
    db.commit()

    page = client.get(f"{PROGRAM}/2026-08-24").text

    assert "Понедельник" in page
    assert "Вторник" not in page


def test_evening_item_lands_in_the_moscow_day(
    client, db, user_factory, session_factory, monkeypatch
):
    """22:00 UTC — уже следующий день по Москве, и в календаре тоже."""
    _freeze_today(monkeypatch, date(2026, 8, 21))
    admin = _staff_client(client, user_factory, session_factory)
    db.add(
        TrackerTask(
            title="Поздний элемент",
            kind=ITEM_VIDEO,
            due_at=datetime(2026, 8, 24, 22, 0, tzinfo=timezone.utc),
            created_by_id=admin.id,
        )
    )
    db.commit()

    assert "Поздний элемент" in client.get(f"{PROGRAM}/2026-08-25").text
    assert "Поздний элемент" not in client.get(f"{PROGRAM}/2026-08-24").text


def test_nonsense_day_gives_404(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    assert client.get(f"{PROGRAM}/2026-13-40").status_code == 404


def test_item_can_be_removed_from_the_day(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze_today(monkeypatch, date(2026, 8, 21))
    admin = _staff_client(client, user_factory, session_factory)
    task = TrackerTask(
        title="Лишний",
        kind=ITEM_VIDEO,
        due_at=datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
        created_by_id=admin.id,
    )
    db.add(task)
    db.commit()

    assert client.post(f"{PROGRAM}/items/{task.id}/delete").status_code == 200
    db.expire_all()
    assert db.get(TrackerTask, task.id).deleted_at is not None
    assert "Лишний" not in client.get(f"{PROGRAM}/2026-08-24").text
