"""Периоды программы (владелец 06.10.2026): Период → Этап → Цикл → задания.

Период — `LearningTopic(kind='period')` над этапом, тот же приём, что у этапа
(`test_routes_program_stages.py`). Экран общий с этапами
(`cabinet_program_stages.html`, `level='period'`), настройки те же. Заданий
на периоде нет. Связка «цикл → этап», на которой держится лента ученика, не
меняется — это сторожат тесты ниже.
"""
from datetime import timedelta

from app.models.learning_topic import (
    TOPIC_KIND_PERIOD,
    TOPIC_KIND_STAGE,
    TOPIC_KIND_WEEK,
    LearningTopic,
)
from app.services.tracker import accessible_cycles, cycle_label, stage_cycle_ordinal
from app.services.tz import today_msk

PERIODS_PAGE = "/cabinet/staff/program/periods"
STAGES_PAGE = "/cabinet/staff/program/stages"
CYCLES_PAGE = "/cabinet/staff/program/cycles"


def _payload(**over):
    data = {
        "title": "Предобучение",
        "description": None,
        "starts_on": (today_msk() - timedelta(days=5)).isoformat(),
        "ends_on": (today_msk() + timedelta(days=30)).isoformat(),
        "is_published": True,
    }
    data.update(over)
    return data


def _of_kind(db, kind):
    return db.query(LearningTopic).filter(LearningTopic.kind == kind).all()


def _period(client, db, **over):
    assert client.post(PERIODS_PAGE, json=_payload(**over)).status_code == 200
    return sorted(_of_kind(db, TOPIC_KIND_PERIOD), key=lambda t: t.id)[-1]


def _stage(client, db, **over):
    # Этапа без периода не бывает (владелец 06.10.2026) — свой период, если
    # тест его не выбрал.
    if "period_id" not in over:
        over["period_id"] = _period(client, db, title="Период этапа").id
    assert client.post(STAGES_PAGE, json=_payload(**over)).status_code == 200
    return sorted(_of_kind(db, TOPIC_KIND_STAGE), key=lambda t: t.id)[-1]


# ── доступ ──────────────────────────────────────────────────────────────────

def test_student_cannot_open_periods(auth_client):
    client, _ = auth_client
    assert client.get(PERIODS_PAGE, follow_redirects=False).status_code in (302, 403)


def test_admin_opens_periods(admin_client):
    client, _ = admin_client
    resp = client.get(PERIODS_PAGE)
    assert resp.status_code == 200
    assert "Периоды программы" in resp.text
    assert 'href="/cabinet/staff/program/periods"' in resp.text


def test_tabs_link_periods_from_stages(admin_client):
    client, _ = admin_client
    resp = client.get(STAGES_PAGE)
    assert 'href="/cabinet/staff/program/periods"' in resp.text


# ── период ──────────────────────────────────────────────────────────────────

def test_admin_creates_period(admin_client, db):
    client, _ = admin_client
    period = _period(client, db)
    assert period.title == "Предобучение"
    assert period.kind == TOPIC_KIND_PERIOD
    assert period.parent_id is None
    assert period.is_published is True


def test_admin_edits_period(admin_client, db):
    client, _ = admin_client
    period = _period(client, db)
    resp = client.post(
        f"{PERIODS_PAGE}/{period.id}", json=_payload(title="1 семестр", is_published=False)
    )
    assert resp.status_code == 200
    db.refresh(period)
    assert period.title == "1 семестр"
    assert period.is_published is False


def test_editing_stage_as_period_404(admin_client, db):
    """Адрес периода не правит этап — `kinds` в `get_topic`."""
    client, _ = admin_client
    stage = _stage(client, db)
    assert client.post(f"{PERIODS_PAGE}/{stage.id}", json=_payload()).status_code == 404


def test_period_payload_rejects_period_id(admin_client, db):
    """У периода нет родителя — лишнее поле отбивается схемой."""
    client, _ = admin_client
    resp = client.post(PERIODS_PAGE, json=_payload(period_id=1))
    assert resp.status_code == 422
    assert _of_kind(db, TOPIC_KIND_PERIOD) == []


def test_period_end_before_start_is_rejected(admin_client, db):
    client, _ = admin_client
    resp = client.post(
        PERIODS_PAGE,
        json=_payload(
            starts_on=(today_msk() + timedelta(days=10)).isoformat(),
            ends_on=today_msk().isoformat(),
        ),
    )
    assert resp.status_code == 422
    assert _of_kind(db, TOPIC_KIND_PERIOD) == []


# ── этап внутри периода ─────────────────────────────────────────────────────

def test_stage_is_created_inside_period(admin_client, db):
    client, _ = admin_client
    period = _period(client, db)
    stage = _stage(client, db, period_id=period.id)
    assert stage.parent_id == period.id


def test_stage_with_missing_period_is_rejected(admin_client, db):
    client, _ = admin_client
    resp = client.post(STAGES_PAGE, json=_payload(period_id=999999))
    assert resp.status_code == 422
    assert _of_kind(db, TOPIC_KIND_STAGE) == []


def test_stage_cannot_be_put_into_another_stage(admin_client, db):
    client, _ = admin_client
    other = _stage(client, db, title="Другой этап")
    resp = client.post(STAGES_PAGE, json=_payload(period_id=other.id))
    assert resp.status_code == 422


def test_stage_without_period_is_rejected(admin_client, db):
    """Владелец 06.10.2026: «к периоду привязывается этап» — ни пропущенного
    поля, ни пустого значения сервер при создании не принимает."""
    client, _ = admin_client
    for body in (_payload(), _payload(period_id=None)):
        resp = client.post(STAGES_PAGE, json=body)
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Выберите период этапа"
    assert _of_kind(db, TOPIC_KIND_STAGE) == []


def test_editing_stage_moves_period_but_never_unlinks(admin_client, db):
    client, _ = admin_client
    first = _period(client, db, title="Предобучение")
    second = _period(client, db, title="1 семестр")
    stage = _stage(client, db, period_id=first.id)

    client.post(f"{STAGES_PAGE}/{stage.id}", json=_payload(period_id=second.id))
    db.refresh(stage)
    assert stage.parent_id == second.id

    resp = client.post(f"{STAGES_PAGE}/{stage.id}", json=_payload(period_id=None, title="Другое"))
    assert resp.status_code == 422
    db.refresh(stage)
    assert stage.parent_id == second.id
    assert stage.title == "Предобучение"


def test_stage_form_requires_period(admin_client, db):
    client, _ = admin_client
    resp = client.get(STAGES_PAGE)
    assert "Без периода" not in resp.text
    assert "Периодов пока нет" in resp.text

    _period(client, db, title="1 семестр")
    resp = client.get(STAGES_PAGE)
    assert '<option value="">Выберите период</option>' in resp.text
    assert "Периодов пока нет" not in resp.text


def test_editing_stage_without_period_field_keeps_link(admin_client, db):
    """Вкладка, открытая до выкатки, шлёт этап без `period_id` — привязка
    остаётся, правка дат её не отвязывает."""
    client, _ = admin_client
    period = _period(client, db)
    stage = _stage(client, db, period_id=period.id)

    resp = client.post(f"{STAGES_PAGE}/{stage.id}", json=_payload(title="Новое название"))
    assert resp.status_code == 200
    db.refresh(stage)
    assert stage.title == "Новое название"
    assert stage.parent_id == period.id


def test_period_page_counts_stages_and_links_filter(admin_client, db):
    client, _ = admin_client
    period = _period(client, db)
    _stage(client, db, period_id=period.id, title="Этап А")
    _stage(client, db, period_id=period.id, title="Этап Б")

    resp = client.get(PERIODS_PAGE)
    assert "2 этапа внутри" in resp.text
    assert f"/cabinet/staff/program/stages?period={period.id}" in resp.text
    # Заданий на периоде нет (владелец 06.10.2026).
    assert "Задания периода" not in resp.text


def test_stages_filter_by_period(admin_client, db):
    client, _ = admin_client
    period = _period(client, db)
    inside = _stage(client, db, period_id=period.id, title="Внутри")
    other = _period(client, db, title="Другой период")
    outside = _stage(client, db, period_id=other.id, title="Снаружи")

    resp = client.get(f"{STAGES_PAGE}?period={period.id}")
    assert resp.status_code == 200
    assert f'data-stage-id="{inside.id}"' in resp.text
    assert f'data-stage-id="{outside.id}"' not in resp.text
    assert "Этапы периода «Предобучение»" in resp.text

    # Чужой номер — весь список, а не пустая страница.
    resp = client.get(f"{STAGES_PAGE}?period={inside.id}")
    assert f'data-stage-id="{outside.id}"' in resp.text


def test_stage_card_shows_period_badge(admin_client, db):
    client, _ = admin_client
    period = _period(client, db, title="1 семестр")
    _stage(client, db, period_id=period.id, title="Октябрь")
    resp = client.get(STAGES_PAGE)
    assert '<span class="prg-badge">1 семестр</span>' in resp.text


# ── сторона ученика не меняется ─────────────────────────────────────────────

def _cycle(client, db, *, stage_id, offset):
    client.post(
        CYCLES_PAGE,
        json={
            "title": "",
            "description": None,
            "starts_on": (today_msk() + timedelta(days=offset)).isoformat(),
            "ends_on": (today_msk() + timedelta(days=offset + 6)).isoformat(),
            "is_published": True,
            "stage_id": stage_id,
        },
    )
    return sorted(_of_kind(db, TOPIC_KIND_WEEK), key=lambda t: t.opens_at)[-1]


def test_cycles_inside_period_keep_numbering(admin_client, db):
    client, _ = admin_client
    period = _period(client, db)
    stage = _stage(client, db, period_id=period.id, title="")
    _cycle(client, db, stage_id=stage.id, offset=-5)
    second = _cycle(client, db, stage_id=stage.id, offset=2)

    assert stage_cycle_ordinal(db, second) == 2
    assert cycle_label(db, second) == "Цикл 2"
    # Этап без названия внутри периода подписан датами: «Цикл N» — только у циклов.
    assert "Цикл" not in cycle_label(db, stage)


def test_period_never_counts_as_student_cycle(admin_client, db, regular_user):
    client, _ = admin_client
    period = _period(client, db)
    stage = _stage(client, db, period_id=period.id)
    cycle = _cycle(client, db, stage_id=stage.id, offset=-1)

    ids = {topic.id for topic in accessible_cycles(db, regular_user.id)}
    assert cycle.id in ids
    assert period.id not in ids
    assert stage.id not in ids


def test_cycles_page_does_not_list_period(admin_client, db):
    client, _ = admin_client
    period = _period(client, db)
    resp = client.get(CYCLES_PAGE)
    assert f'data-cycle-id="{period.id}"' not in resp.text
    # И в выборе этапа у цикла периода нет.
    assert f'<option value="{period.id}"' not in resp.text
