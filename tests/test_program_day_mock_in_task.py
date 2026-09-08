"""Пробник собирается внутри «Задания», а не отдельной плиткой.

Владелец 08.09.2026: «внутри должна быть логика создания ПРОБНИКА!!!».
Плитка так и осталась одна (её стережёт `test_program_day_script.py` и
`test_routes_program_calendar.py`) — билеты живут секцией внутри формы
«Задания», и когда они есть, форма уходит на `/{iso}/mock`.

Сервер под это не менялся: создание пробника покрыто
`test_routes_program_mock.py`. Здесь проверяется шов — что разметка секции
на месте и что payload ровно той формы, которую собирает скрипт страницы,
роут принимает.
"""

from datetime import date, datetime, timezone
import re

from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.models.task_block import TaskBlock
from app.models.tracker import SOURCE_EXAM_ASSIGNMENT, TrackerTask

PROGRAM = "/cabinet/staff/program"
TODAY = date(2026, 8, 21)
FUTURE_DAY = "2026-08-24"


def _staff_client(client, user_factory, session_factory):
    user = user_factory(
        vk_id=530_101,
        name="Главный преподаватель",
        is_admin=True,
        is_group_member=False,
        role_name="админ",
    )
    client.cookies.set("session_id", session_factory(user).id)
    return user


def _freeze(monkeypatch, value: date = TODAY):
    monkeypatch.setattr("app.api.cabinet_program.today_msk", lambda: value)
    monkeypatch.setattr("app.services.program.today_msk", lambda: value)
    monkeypatch.setattr("app.services.exam_tickets.today_msk", lambda: value)
    monkeypatch.setattr(
        "app.services.exam_tickets.now_utc",
        lambda: datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc),
    )


def _task_form(page: str) -> str:
    """Кусок разметки одной формы «Задания» — от её тега до закрывающего."""
    start = page.index('data-simple-kind="material"')
    return page[start:page.index("</form>", start)]


# ── Разметка ──────────────────────────────────────────────────────────────

def test_task_form_carries_the_ticket_section(
    client, user_factory, session_factory, monkeypatch
):
    """Секция билетов и кнопка её открытия — внутри формы «Задания»."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    page = client.get(f"{PROGRAM}/{FUTURE_DAY}").text
    form = _task_form(page)

    assert "data-add-mock" in form
    assert "data-simple-mock" in form
    # Делегированные обработчики билетов ищут именно эти атрибуты: без них
    # загрузка фотографий и нумерация молча перестанут работать.
    for attribute in (
        "data-subject-block",
        "data-subject-images",
        "data-subject-status",
        "data-subject-summary",
        "data-tickets",
    ):
        assert attribute in form


def test_other_forms_have_no_ticket_section(
    client, user_factory, session_factory, monkeypatch
):
    """Анкета, тест, занятие и чек-лист билетов не принимают.

    Эти формы существуют только для правки уже стоящих элементов, у них нет
    ни `ExamAssignment`, ни предмета NOT NULL.
    """
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    page = client.get(f"{PROGRAM}/{FUTURE_DAY}").text

    for kind in ("survey", "quiz", "lesson", "checklist"):
        start = page.index(f'data-simple-kind="{kind}"')
        form = page[start:page.index("</form>", start)]
        assert "data-add-mock" not in form
        assert "data-simple-mock" not in form


def test_ticket_section_functions_are_declared(
    client, user_factory, session_factory, monkeypatch
):
    """Каждая функция секции объявлена в том же скрипте, что её вызывает.

    Тот же класс проверки, что `test_program_day_script.py`: страница
    отдаётся с кодом 200 и когда скрипт падает на первом нажатии.
    """
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    page = client.get(f"{PROGRAM}/{FUTURE_DAY}").text
    declared = set(re.findall(r"function (\w+)", page))

    for name in (
        "simpleMockSection",
        "simpleMockTickets",
        "collectSimpleTickets",
        "showSimpleMock",
        "resetSimpleMock",
    ):
        assert name in declared, f"{name} вызывается, но не объявлена"


# ── Шов с роутом ──────────────────────────────────────────────────────────

def test_payload_of_the_task_form_creates_a_mock(
    client, db, user_factory, session_factory, monkeypatch
):
    """Ровно то, что шлёт форма «Задания» с билетами.

    Один предмет (решение владельца 08.09.2026), блоки конструктора рядом с
    билетами и **никакого `starts_on`**: окно сдачи ставит сервер из
    `default_schedule_for_day`, а лишнее поле уронило бы запрос в 422 —
    у `MockPayload` стоит `extra="forbid"`.
    """
    _freeze(monkeypatch)
    admin = _staff_client(client, user_factory, session_factory)

    response = client.post(
        f"{PROGRAM}/{FUTURE_DAY}/mock",
        json={
            "title": "Пробник по рисунку",
            "description": "Два листа",
            "subjects": [
                {
                    "subject": "Рисунок",
                    "note": None,
                    "tickets": [
                        {
                            "title": "Рисунок · Билет 1",
                            "description": "",
                            "image_url": None,
                            "image_path": None,
                        }
                    ],
                }
            ],
            "is_required": True,
            "blocks": [{"block_type": "text", "body": "Materials на столе"}],
            "audience": {
                "assign_to_all": True,
                "tag_ids": [],
                "assignee_usernames": "",
            },
        },
    )

    assert response.status_code == 200, response.text
    assignment = db.query(ExamAssignment).one()
    assert assignment.subject == "Рисунок"
    assert assignment.kind == "mock"
    assert assignment.created_by_id == admin.id
    assert db.query(ExamTicket).count() == 1

    task = (
        db.query(TrackerTask)
        .filter(
            TrackerTask.source_kind == SOURCE_EXAM_ASSIGNMENT,
            TrackerTask.source_id == assignment.id,
        )
        .one()
    )
    assert task.kind == "mock_exam"
    assert task.is_published
    # Блоки конструктора доехали вместе с билетом — ради этого пробник и
    # переехал внутрь «Задания».
    blocks = db.query(TaskBlock).filter(TaskBlock.task_id == task.id).all()
    assert [b.body for b in blocks] == ["Materials на столе"]


def test_starts_on_together_with_tickets_is_refused(
    client, user_factory, session_factory, monkeypatch
):
    """Сторож на случай, если форма однажды пришлёт «Откроется не раньше».

    Поле у пробника не действует — окно берётся из дня, — и роут его не
    принимает. Пусть падение будет здесь, а не у владельца на экране.
    """
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    response = client.post(
        f"{PROGRAM}/{FUTURE_DAY}/mock",
        json={
            "title": "Пробник по рисунку",
            "subjects": [
                {"subject": "Рисунок", "note": None, "tickets": [{"title": "Билет 1"}]}
            ],
            "starts_on": "2026-08-25",
            "audience": {
                "assign_to_all": True,
                "tag_ids": [],
                "assignee_usernames": "",
            },
        },
    )

    assert response.status_code == 422
