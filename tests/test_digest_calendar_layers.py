"""Раскладка компактного календаря дайджеста на слои (владелец 04.10.2026).

Макет «Путь к сотке»: однодневное событие — кружок вокруг числа, период —
таблетка через дни, заливкой или контуром по типу. Слои дня считает
`services/tracker.py::digest_calendar`, сетка в шаблоне только рисует.
Апрель 2026 взят из макета: 1-е — среда, 30 марта — понедельник.
"""

from datetime import date

from app.services.schedule_event_types import create_type, seed_default_types, list_types
from app.services.tracker import create_digest, create_event, digest_calendar, list_events


def _setup(db, user_factory):
    author = user_factory(vk_id=440_001, name="ГП", role_name="админ")
    digest = create_digest(
        db, title="Апрель", year=2026, month=4, assign_to_all=True, user_id=author.id,
    )
    types = {
        "fill": create_type(db, name="Отработка", color="sky", style="fill"),
        "ring": create_type(db, name="Загрузка работ", color="pink", style="ring"),
        "lesson": create_type(db, name="Занятие", color="violet", style="fill"),
        "meeting": create_type(db, name="Встреча", color="violet", style="ring"),
    }
    return digest, types


def _add(db, digest, event_type, title, starts, ends=None):
    return create_event(
        db, digest.id, type_id=event_type.id, title=title, note=None,
        starts_on=starts, ends_on=ends or starts, meeting_url=None,
    )


def _days(db, digest):
    db.flush()
    return {day["iso"]: day for day in digest_calendar(digest, list_events(db, digest.id), today=date(2026, 4, 15))}


def test_period_becomes_a_pill_cut_by_week_rows(db, user_factory):
    digest, types = _setup(db, user_factory)
    _add(db, digest, types["ring"], "Загрузка работ", date(2026, 4, 3), date(2026, 4, 10))

    days = _days(db, digest)

    assert days["2026-04-03"]["ring"] == {"color": "pink", "pos": "start"}
    assert days["2026-04-04"]["ring"]["pos"] == "mid"
    # Воскресенье закрывает строку, понедельник открывает следующую.
    assert days["2026-04-05"]["ring"]["pos"] == "end"
    assert days["2026-04-06"]["ring"]["pos"] == "start"
    assert days["2026-04-10"]["ring"]["pos"] == "end"
    assert days["2026-04-11"]["ring"] is None
    assert days["2026-04-03"]["fill"] is None


def test_single_day_is_a_dot_on_top_of_a_period(db, user_factory):
    """9-е в макете: кружок занятия внутри контура загрузки работ."""
    digest, types = _setup(db, user_factory)
    _add(db, digest, types["ring"], "Загрузка работ", date(2026, 4, 6), date(2026, 4, 10))
    _add(db, digest, types["lesson"], "Групповой урок", date(2026, 4, 9))

    day = _days(db, digest)["2026-04-09"]

    assert day["dot"] == {"color": "violet", "style": "fill"}
    assert day["ring"] == {"color": "pink", "pos": "mid"}
    assert day["extra"] == []


def test_ring_style_single_day_is_an_outlined_dot(db, user_factory):
    digest, types = _setup(db, user_factory)
    _add(db, digest, types["meeting"], "Стратегическая встреча", date(2026, 4, 27))

    assert _days(db, digest)["2026-04-27"]["dot"] == {"color": "violet", "style": "ring"}


def test_period_from_previous_month_is_painted_in_the_grid(db, user_factory):
    """«31 – 1» в макете: событие апрельского дайджеста, начатое в марте,
    видно и на мартовских днях первой строки."""
    digest, types = _setup(db, user_factory)
    _add(db, digest, types["fill"], "Отработка пробника", date(2026, 3, 30), date(2026, 4, 1))

    days = _days(db, digest)

    assert days["2026-03-30"]["in_month"] is False
    assert days["2026-03-30"]["fill"] == {"color": "sky", "pos": "start"}
    assert days["2026-03-31"]["fill"]["pos"] == "mid"
    assert days["2026-04-01"]["fill"]["pos"] == "end"


def test_shorter_period_wins_the_layer_and_day_is_flagged(db, user_factory):
    digest, types = _setup(db, user_factory)
    long_one = create_type(db, name="Курс", color="gray", style="fill")
    _add(db, digest, long_one, "Весь месяц", date(2026, 4, 1), date(2026, 4, 30))
    _add(db, digest, types["fill"], "Неделя", date(2026, 4, 13), date(2026, 4, 17))

    days = _days(db, digest)

    assert days["2026-04-14"]["fill"]["color"] == "sky"
    # Спрятанный длинный период — точка его цвета под числом.
    assert days["2026-04-14"]["extra"] == [{"color": "gray"}]
    assert days["2026-04-20"]["fill"]["color"] == "gray"
    assert days["2026-04-20"]["extra"] == []


def test_each_other_event_of_the_day_is_a_dot(db, user_factory):
    """Служба заботы 04.10.2026: «если в один день несколько событий, то
    ребёнку нужно видеть это в календаре, а то у него только один цвет».
    Прод, 11.10: публикация и три занятия. Владелец 05.10.2026: показывать,
    что событие не одно, как в календаре айфона, а что именно — в окне по
    тапу. Точка — на событие: три занятия — три точки."""
    digest, types = _setup(db, user_factory)
    publish = create_type(db, name="Публикация", color="sky", style="fill")
    _add(db, digest, publish, "2 неделя", date(2026, 4, 11))
    for title in ("Композиция", "Рисунок", "Р+К очно"):
        _add(db, digest, types["lesson"], title, date(2026, 4, 11))

    day = _days(db, digest)["2026-04-11"]

    assert day["dot"]["color"] == "sky"
    assert day["extra"] == [{"color": "violet"}] * 3


def test_same_type_second_event_is_a_dot_too(db, user_factory):
    """Два занятия одного типа — кружок и точка того же цвета: событий два."""
    digest, types = _setup(db, user_factory)
    for title in ("Композиция", "Рисунок"):
        _add(db, digest, types["lesson"], title, date(2026, 4, 18))

    day = _days(db, digest)["2026-04-18"]

    assert day["dot"]["color"] == "violet"
    assert day["extra"] == [{"color": "violet"}]


def test_dots_are_capped_at_three(db, user_factory):
    """Больше трёх точек под числом не помещается; все события дня — в окне."""
    digest, types = _setup(db, user_factory)
    colours = ("sky", "pink", "mint", "teal", "coral")
    for index, colour in enumerate(colours):
        kind = create_type(db, name=f"Тип {index}", color=colour, style="fill")
        _add(db, digest, kind, f"Событие {index}", date(2026, 4, 22))

    day = _days(db, digest)["2026-04-22"]

    assert day["dot"]["color"] == "sky"
    assert [extra["color"] for extra in day["extra"]] == ["pink", "mint", "teal"]


def test_one_day_period_is_solo(db, user_factory):
    """Период на одну строку недели из одного дня рисуется целой таблеткой."""
    digest, types = _setup(db, user_factory)
    # Воскресенье 12-е и понедельник 13-е: на каждой строке по одному дню.
    _add(db, digest, types["fill"], "Переход недели", date(2026, 4, 12), date(2026, 4, 13))

    days = _days(db, digest)

    assert days["2026-04-12"]["fill"]["pos"] == "solo"
    assert days["2026-04-13"]["fill"]["pos"] == "solo"


def test_seed_default_types_fills_only_an_empty_table(db):
    seed_default_types(db)
    names = [t.name for t in list_types(db)]
    assert names == [
        "Публикация уроков и заданий", "Дедлайн", "Обратная связь", "Пробник",
        "Занятие", "Период сдачи контрольных", "Общий эфир",
    ]
    seed_default_types(db)
    assert len(list_types(db)) == 7
