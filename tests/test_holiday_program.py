"""Каникулярный набор: две программы школы, годовой курс и каникулярный.

Созвон 13.10.2026, план `plans/2026-10-14-apparchi-созвон-13-10.md`, часть 3.
Признак программы стоит у периода (`LearningTopic.program`) и у ученика
(`User.program`); этап и цикл получают его по цепочке `parent_id`. Годовой
ученик не видит каникулярного и наоборот — правило в одной точке,
`video_topics.accessible_topic_ids`.
"""
from datetime import timedelta, timezone

from app.constants import PROGRAM_ANNUAL, PROGRAM_HOLIDAY
from app.models.learning_topic import (
    TOPIC_KIND_PERIOD,
    TOPIC_KIND_STAGE,
    TOPIC_KIND_WEEK,
    LearningTopic,
)
from app.models.tag import Tag, UserTag
from app.services.cycle_stats import cycle_debtors
from app.services.tracker import (
    accessible_task_ids,
    accessible_cycles,
    count_task_audience,
    create_task,
    effective_cycle,
    task_audience_user_ids,
)
from app.services.tz import msk_midnight, today_msk
from app.services.video_topics import (
    accessible_topic_ids,
    set_topic_tags,
    topic_audience_user_ids,
    topic_program,
)

TODAY = today_msk()


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _topic(db, owner, *, title, kind=TOPIC_KIND_WEEK, parent=None, program=PROGRAM_ANNUAL,
           starts=-2, ends=5):
    topic = LearningTopic(
        title=title,
        opens_at=_utc(msk_midnight(TODAY + timedelta(days=starts))),
        ends_at=_utc(msk_midnight(TODAY + timedelta(days=ends)) + timedelta(hours=23)),
        assign_to_all=True,
        is_published=True,
        kind=kind,
        program=program,
        created_by_id=owner.id,
        parent_id=parent.id if parent is not None else None,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _program(db, owner, program, title):
    """Период → этап → цикл с обязательным заданием."""
    period = _topic(db, owner, title=f"{title} период", kind=TOPIC_KIND_PERIOD, program=program)
    stage = _topic(db, owner, title=f"{title} этап", kind=TOPIC_KIND_STAGE, parent=period)
    cycle = _topic(db, owner, title=f"{title} цикл", parent=stage)
    task = create_task(
        db, title=f"Задание {title}", user_id=owner.id, kind="material",
        topic_id=cycle.id, assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    return period, stage, cycle, task


def _holiday_student(db, user_factory, *, vk_id=200_001, name="Каникулярный"):
    student = user_factory(vk_id=vk_id, name=name, tariff="")
    student.program = PROGRAM_HOLIDAY
    db.commit()
    return student


def test_annual_student_does_not_see_holiday_program(db, regular_user, admin_user):
    annual = _program(db, admin_user, PROGRAM_ANNUAL, "Годовой")
    holiday = _program(db, admin_user, PROGRAM_HOLIDAY, "Каникулы")

    visible = accessible_topic_ids(db, regular_user.id)

    assert {t.id for t in annual[:3]} <= visible
    assert not {t.id for t in holiday[:3]} & visible
    assert [c.id for c in accessible_cycles(db, regular_user.id)] == [annual[2].id]


def test_holiday_student_sees_only_holiday_program(db, user_factory, admin_user):
    annual = _program(db, admin_user, PROGRAM_ANNUAL, "Годовой")
    holiday = _program(db, admin_user, PROGRAM_HOLIDAY, "Каникулы")
    # Легаси-цикл без этапа и тема каталога видео — годовые.
    legacy = _topic(db, admin_user, title="Легаси")
    student = _holiday_student(db, user_factory)

    visible = accessible_topic_ids(db, student.id)

    assert {t.id for t in holiday[:3]} <= visible
    assert not {t.id for t in annual[:3]} & visible
    assert legacy.id not in visible
    assert effective_cycle(db, student.id, TODAY).id == holiday[2].id


def test_holiday_debt_does_not_lock_annual_and_back(db, regular_user, user_factory, admin_user):
    """Долги двух программ не смешиваются: годовой стоит на годовом цикле,
    каникулярный — на каникулярном, хотя оба цикла идут в одни даты."""
    annual = _program(db, admin_user, PROGRAM_ANNUAL, "Годовой")
    holiday = _program(db, admin_user, PROGRAM_HOLIDAY, "Каникулы")
    student = _holiday_student(db, user_factory)

    assert effective_cycle(db, regular_user.id, TODAY).id == annual[2].id
    assert effective_cycle(db, student.id, TODAY).id == holiday[2].id


def test_topic_program_follows_the_chain(db, admin_user):
    holiday = _program(db, admin_user, PROGRAM_HOLIDAY, "Каникулы")
    annual = _program(db, admin_user, PROGRAM_ANNUAL, "Годовой")
    legacy = _topic(db, admin_user, title="Легаси")

    assert [topic_program(db, t) for t in holiday[:3]] == [PROGRAM_HOLIDAY] * 3
    assert [topic_program(db, t) for t in annual[:3]] == [PROGRAM_ANNUAL] * 3
    assert topic_program(db, legacy) == PROGRAM_ANNUAL


def test_holiday_cycle_by_tag_reaches_only_its_group(db, user_factory, admin_user):
    """Каждой группе свой цикл, доступ по тегу курса."""
    _, stage, cycle, _ = _program(db, admin_user, PROGRAM_HOLIDAY, "Каникулы")
    group_b = _topic(db, admin_user, title="Группа Б", parent=stage)
    tag_a = Tag(name="Каникулы А")
    tag_b = Tag(name="Каникулы Б")
    db.add_all([tag_a, tag_b])
    db.commit()
    cycle.assign_to_all = False
    group_b.assign_to_all = False
    set_topic_tags(db, cycle, [tag_a.id])
    set_topic_tags(db, group_b, [tag_b.id])
    student_a = _holiday_student(db, user_factory, vk_id=200_001, name="А")
    student_b = _holiday_student(db, user_factory, vk_id=200_002, name="Б")
    db.add_all([UserTag(user_id=student_a.id, tag_id=tag_a.id),
                UserTag(user_id=student_b.id, tag_id=tag_b.id)])
    db.commit()

    assert cycle.id in accessible_topic_ids(db, student_a.id)
    assert group_b.id not in accessible_topic_ids(db, student_a.id)
    assert topic_audience_user_ids(db, cycle.id) == {student_a.id}
    assert [d["user"].id for d in cycle_debtors(db, cycle)] == [student_a.id]


def test_audiences_split_by_program(db, regular_user, user_factory, admin_user):
    annual = _program(db, admin_user, PROGRAM_ANNUAL, "Годовой")
    holiday = _program(db, admin_user, PROGRAM_HOLIDAY, "Каникулы")
    student = _holiday_student(db, user_factory)

    assert topic_audience_user_ids(db, annual[2].id) == {regular_user.id}
    assert topic_audience_user_ids(db, holiday[2].id) == {student.id}
    assert task_audience_user_ids(db, annual[3].id) == {regular_user.id}
    assert [d["user"].id for d in cycle_debtors(db, annual[2])] == [regular_user.id]
    assert [d["user"].id for d in cycle_debtors(db, holiday[2])] == [student.id]


def test_one_off_task_for_all_is_annual_only(db, regular_user, user_factory, admin_user):
    """Разовая задача «всем» вне программы — годового курса."""
    task = create_task(db, title="Разовая", user_id=admin_user.id, kind="material",
                       assign_to_all=True)
    task.is_published = True
    db.commit()
    student = _holiday_student(db, user_factory)

    assert task.id in accessible_task_ids(db, regular_user.id)
    assert task.id not in accessible_task_ids(db, student.id)
    assert task_audience_user_ids(db, task.id) == {regular_user.id}
    assert count_task_audience(db, assign_to_all=True, tag_ids=[], assignee_ids=[]) == 1


# ── Конструктор: переключатель программ ─────────────────────────────────────

PERIODS_PAGE = "/cabinet/staff/program/periods"
STAGES_PAGE = "/cabinet/staff/program/stages"
CYCLES_PAGE = "/cabinet/staff/program/cycles"


def _dates(**over):
    data = {
        "title": "",
        "starts_on": (TODAY - timedelta(days=1)).isoformat(),
        "ends_on": (TODAY + timedelta(days=6)).isoformat(),
        "is_published": True,
    }
    data.update(over)
    return data


def test_new_period_takes_the_program_of_the_screen(admin_client, db):
    client, _ = admin_client

    resp = client.post(PERIODS_PAGE, json=_dates(title="Каникулы", program=PROGRAM_HOLIDAY))
    assert resp.status_code == 200
    period = db.get(LearningTopic, resp.json()["period_id"])
    assert period.program == PROGRAM_HOLIDAY

    # Правка программу не меняет: перенос готового периода сменил бы
    # ученикам все его циклы.
    resp = client.post(f"{PERIODS_PAGE}/{period.id}",
                       json=_dates(title="Каникулы", program=PROGRAM_ANNUAL))
    assert resp.status_code == 200
    db.refresh(period)
    assert period.program == PROGRAM_HOLIDAY


def test_lists_show_only_the_chosen_program(admin_client, db, admin_user):
    client, _ = admin_client
    annual = _program(db, admin_user, PROGRAM_ANNUAL, "Годовой")
    holiday = _program(db, admin_user, PROGRAM_HOLIDAY, "Каникулы")

    for page, noun in ((PERIODS_PAGE, "период"), (STAGES_PAGE, "этап"), (CYCLES_PAGE, "цикл")):
        text = client.get(f"{page}?program=holiday").text
        assert f"Каникулы {noun}" in text and f"Годовой {noun}" not in text, page
        assert 'aria-current="page">Каникулярный</a>' in text
        text = client.get(f"{page}?program=annual").text
        assert f"Годовой {noun}" in text and f"Каникулы {noun}" not in text, page
    # Внутри периода программа — его собственная, что бы ни было в адресе.
    text = client.get(f"{STAGES_PAGE}?period={holiday[0].id}&program=annual").text
    assert "Каникулы этап" in text


def test_program_choice_is_remembered(admin_client, db, admin_user):
    client, _ = admin_client
    _program(db, admin_user, PROGRAM_HOLIDAY, "Каникулы")

    resp = client.get(f"{CYCLES_PAGE}?program=holiday")
    assert "prg_program=holiday" in resp.headers["set-cookie"]
    client.cookies.set("prg_program", "holiday")
    assert "Каникулы цикл" in client.get(CYCLES_PAGE).text


def test_holiday_cycle_needs_a_holiday_stage(admin_client, db, admin_user):
    client, _ = admin_client
    annual = _program(db, admin_user, PROGRAM_ANNUAL, "Годовой")

    for stage_id in (None, annual[1].id):
        resp = client.post(CYCLES_PAGE, json=_dates(stage_id=stage_id, program=PROGRAM_HOLIDAY))
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Выберите этап каникулярной программы"


def test_holiday_cycle_is_addressed_by_tags_not_tariffs(admin_client, db, admin_user):
    client, _ = admin_client
    holiday = _program(db, admin_user, PROGRAM_HOLIDAY, "Каникулы")
    annual = _program(db, admin_user, PROGRAM_ANNUAL, "Годовой")
    tag = Tag(name="Группа 1")
    db.add(tag)
    db.commit()

    page = client.get(f"{CYCLES_PAGE}?program=holiday").text
    assert f'data-c-tag="{tag.id}"' in page and "data-c-tariff=" not in page
    assert "data-c-tag=" not in client.get(f"{CYCLES_PAGE}?program=annual").text

    resp = client.post(CYCLES_PAGE, json=_dates(
        stage_id=holiday[1].id, program=PROGRAM_HOLIDAY, tag_ids=[tag.id],
    ))
    assert resp.status_code == 200
    cycle = db.get(LearningTopic, resp.json()["cycle_id"])
    assert cycle.assign_to_all is False
    assert not cycle.tariff_restricted
    from app.services.video_topics import get_tag_ids
    assert get_tag_ids(db, cycle.id) == [tag.id]
    assert "Группа 1" in client.get(f"{CYCLES_PAGE}?program=holiday").text

    # Без тегов — всем каникулярным.
    resp = client.post(f"{CYCLES_PAGE}/{cycle.id}", json=_dates(
        stage_id=holiday[1].id, program=PROGRAM_HOLIDAY, tag_ids=[],
    ))
    assert resp.status_code == 200
    db.refresh(cycle)
    assert cycle.assign_to_all is True

    # Перенос в годовой этап снимает теги прежней программы.
    client.post(f"{CYCLES_PAGE}/{cycle.id}", json=_dates(
        stage_id=holiday[1].id, program=PROGRAM_HOLIDAY, tag_ids=[tag.id],
    ))
    resp = client.post(f"{CYCLES_PAGE}/{cycle.id}", json=_dates(
        stage_id=annual[1].id, program=PROGRAM_ANNUAL, tariff_access=None,
    ))
    assert resp.status_code == 200
    db.refresh(cycle)
    assert cycle.assign_to_all is True
    assert get_tag_ids(db, cycle.id) == []


def test_holiday_cycle_editor_has_no_tariffs(admin_client, db, admin_user):
    """У каникулярных учеников тарифа нет: блок с отмеченным тарифом они бы
    не увидели, поэтому редактор каникулярного цикла тарифов не рисует."""
    client, _ = admin_client
    holiday = _program(db, admin_user, PROGRAM_HOLIDAY, "Каникулы")
    annual = _program(db, admin_user, PROGRAM_ANNUAL, "Годовой")

    page = client.get(f"{CYCLES_PAGE}/{holiday[2].id}").text
    assert "var HAS_TARIFFS = false;" in page
    assert "<option value=\"Я САМ\">" not in page

    page = client.get(f"{CYCLES_PAGE}/{annual[2].id}").text
    assert "var HAS_TARIFFS = true;" in page
