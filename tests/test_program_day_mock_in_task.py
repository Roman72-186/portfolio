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

from datetime import date, datetime, timedelta, timezone
import re

from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.services.mock_exam_access import MOCK_EXAM_DEFAULT_DURATION_MINUTES
from app.services.tz import MSK_TZ
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

    # Кнопка стоит в общем ряду добавления, рядом с «+ Текст» и «+ Фото»
    # (владелец 08.09.2026: «перенести в общий список добавления кнопок»).
    add_row_start = form.index("prg-blocks-add")
    add_row = form[add_row_start:form.index("</div>", form.index("data-add-mock"))]
    assert "data-add-mock" in add_row
    assert "data-add-block" in add_row
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


# ── Время сдачи (владелец 09.09.2026) ─────────────────────────────────────
#
# «Выбираем название билета, какое-то описание, добавляем фотку и выставляем
# время сдачи». 30.08.2026 эти поля из конструктора убрали, окно ставил сервер;
# теперь они вернулись, оставшись необязательными.

def _mock_json(**extra) -> dict:
    """Минимальный payload формы «Задания» с одним билетом."""
    payload = {
        "title": "Пробник по рисунку",
        "subjects": [
            {
                "subject": "Рисунок",
                "note": None,
                "tickets": [{"title": "Натюрморт с фруктами", "description": "Два листа"}],
            }
        ],
        "audience": {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    }
    payload.update(extra)
    return payload


def test_schedule_from_the_form_lands_on_the_ticket(
    client, db, user_factory, session_factory, monkeypatch
):
    """Заданное время сдачи доезжает до билета без изменений."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    response = client.post(
        f"{PROGRAM}/{FUTURE_DAY}/mock",
        json=_mock_json(schedule={
            "opens_at": f"{FUTURE_DAY}T09:00",
            "closes_at": f"{FUTURE_DAY}T21:00",
            "duration_minutes": 90,
        }),
    )

    assert response.status_code == 200, response.text
    ticket = db.query(ExamTicket).one()
    assert ticket.title == "Натюрморт с фруктами"
    assert ticket.description == "Два листа"
    assert ticket.duration_minutes == 90
    # В базе UTC, в форме московское время: 09:00 МСК = 06:00 UTC.
    assert ticket.opens_at.astimezone(timezone.utc).hour == 6
    assert ticket.closes_at.astimezone(timezone.utc).hour == 18


def test_no_schedule_keeps_the_default_window(
    client, db, user_factory, session_factory, monkeypatch
):
    """Не заполнил поля — прежнее поведение: 11:45–18:30 дня и 240 минут.

    Это и есть страховка от регрессии для преподавателя, который к новым
    полям не притронулся.
    """
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    response = client.post(f"{PROGRAM}/{FUTURE_DAY}/mock", json=_mock_json())

    assert response.status_code == 200, response.text
    ticket = db.query(ExamTicket).one()
    assert ticket.duration_minutes == MOCK_EXAM_DEFAULT_DURATION_MINUTES
    opens_msk = ticket.opens_at.astimezone(MSK_TZ)
    closes_msk = ticket.closes_at.astimezone(MSK_TZ)
    assert (opens_msk.hour, opens_msk.minute) == (11, 45)
    assert (closes_msk.hour, closes_msk.minute) == (18, 30)


def test_form_prefills_the_schedule_fields(
    client, user_factory, session_factory, monkeypatch
):
    """Поля времени стоят заполненными, а не пустыми.

    Пустое `datetime-local` в Safari не рисует ни рамки, ни подсказки формата
    (разбор 08.09.2026) — поле выглядело бы отсутствующим.
    """
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    form = _task_form(client.get(f"{PROGRAM}/{FUTURE_DAY}").text)

    assert "data-mock-schedule" in form
    assert f'data-mock-opens\n               value="{FUTURE_DAY}T11:45"' in form
    assert f'data-mock-closes\n               value="{FUTURE_DAY}T18:30"' in form
    assert f'data-mock-duration\n               value="{MOCK_EXAM_DEFAULT_DURATION_MINUTES}"' in form


def test_window_outside_the_day_is_refused(
    client, user_factory, session_factory, monkeypatch
):
    """Открытие пробника обязано попадать в день задания.

    `datetime-local` даёт выбрать любую дату. Без проверки карточка стояла бы
    в одном дне, а билет открывался в другом: ученик видит задание в ленте,
    жмёт «Начать» и получает «окно ещё не открылось» — и причину не узнаёт ни
    он, ни преподаватель.
    """
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    other_day = (date.fromisoformat(FUTURE_DAY) + timedelta(days=7)).isoformat()

    response = client.post(
        f"{PROGRAM}/{FUTURE_DAY}/mock",
        json=_mock_json(schedule={
            "opens_at": f"{other_day}T11:45",
            "closes_at": f"{other_day}T18:30",
            "duration_minutes": 60,
        }),
    )

    assert response.status_code == 422
    assert "открывается в день задания" in response.json()["detail"]


def test_window_may_cross_midnight(
    client, db, user_factory, session_factory, monkeypatch
):
    """Закрытие на следующий день разрешено: пробник может идти через полночь."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    next_day = (date.fromisoformat(FUTURE_DAY) + timedelta(days=1)).isoformat()

    response = client.post(
        f"{PROGRAM}/{FUTURE_DAY}/mock",
        json=_mock_json(schedule={
            "opens_at": f"{FUTURE_DAY}T20:00",
            "closes_at": f"{next_day}T02:00",
            "duration_minutes": 120,
        }),
    )

    assert response.status_code == 200, response.text
    ticket = db.query(ExamTicket).one()
    assert ticket.start_date.isoformat() == FUTURE_DAY
    assert ticket.end_date.isoformat() == next_day


def test_duration_longer_than_the_window_is_refused_with_a_plain_reason(
    client, user_factory, session_factory, monkeypatch
):
    """240 минут в окне 11:45–18:30 не влезают — и это должно быть сказано.

    Самая вероятная первая ошибка: поля предзаполнены окном в 405 минут, а
    владелец печатает в «минутах» большее число. Молчаливое сохранение здесь
    было бы худшим исходом.
    """
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    response = client.post(
        f"{PROGRAM}/{FUTURE_DAY}/mock",
        json=_mock_json(schedule={
            "opens_at": f"{FUTURE_DAY}T11:45",
            "closes_at": f"{FUTURE_DAY}T18:30",
            "duration_minutes": 480,
        }),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "окно короче времени на работу" in detail
    assert "480" in detail
