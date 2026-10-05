"""Периоды программы (владелец 06.10.2026): Период → Этап → Цикл → задания.

Период — `LearningTopic(kind='period')` над этапом, тот же приём, что у этапа
(`test_routes_program_stages.py`). Экран общий с этапами
(`cabinet_program_stages.html`, `level='period'`), настройки те же. Заданий
на периоде нет. Связка «цикл → этап», на которой держится лента ученика, не
меняется — это сторожат тесты ниже.
"""
from datetime import datetime, timedelta, timezone

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


def _now():
    return datetime.now(timezone.utc)


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
    # Путь сверху: «Периоды › Предобучение», текущий уровень без ссылки.
    assert f'<a href="{PERIODS_PAGE}">Периоды</a> ›' in resp.text
    assert '<span aria-current="page">Предобучение</span>' in resp.text

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


# ── удаление периода и этапа (владелец 06.10.2026) ──────────────────────────
# «Периоды и этапы можно удалять и насовсем, когда мы будем делать их
# тестовыми» — удаляется только пустая рамка, и насовсем, без `deleted_at`.

def _audit_actions(db):
    from app.models.audit_log import AuditLog
    return [row.action for row in db.query(AuditLog).all()]


def test_empty_period_is_deleted_for_good(admin_client, db):
    client, _ = admin_client
    period = _period(client, db, title="ннннннн")
    period_id = period.id

    resp = client.post(f"{PERIODS_PAGE}/{period_id}/delete")
    assert resp.status_code == 200
    db.expire_all()
    assert db.get(LearningTopic, period_id) is None
    assert "program_period_delete" in _audit_actions(db)


def test_period_with_stage_is_not_deleted(admin_client, db):
    client, _ = admin_client
    period = _period(client, db)
    stage = _stage(client, db, period_id=period.id)

    resp = client.post(f"{PERIODS_PAGE}/{period.id}/delete")
    assert resp.status_code == 409
    assert resp.json()["detail"].startswith("В периоде есть этапы")
    db.expire_all()
    assert db.get(LearningTopic, period.id) is not None
    assert db.get(LearningTopic, stage.id).parent_id == period.id


def test_empty_stage_is_deleted_for_good(admin_client, db):
    client, _ = admin_client
    period = _period(client, db)
    stage = _stage(client, db, period_id=period.id, title="Пробный этап")
    stage_id = stage.id

    resp = client.post(f"{STAGES_PAGE}/{stage_id}/delete")
    assert resp.status_code == 200
    db.expire_all()
    assert db.get(LearningTopic, stage_id) is None
    # Период после этого пустой — удаляется следом.
    assert client.post(f"{PERIODS_PAGE}/{period.id}/delete").status_code == 200
    assert "program_stage_delete" in _audit_actions(db)


def test_stage_with_cycle_is_not_deleted(admin_client, db):
    client, _ = admin_client
    stage = _stage(client, db)
    cycle = _cycle(client, db, stage_id=stage.id, offset=1)

    resp = client.post(f"{STAGES_PAGE}/{stage.id}/delete")
    assert resp.status_code == 409
    assert resp.json()["detail"].startswith("В этапе есть циклы")
    db.expire_all()
    assert db.get(LearningTopic, cycle.id).parent_id == stage.id


def test_delete_addresses_do_not_mix_levels(admin_client, db):
    """Адрес периода не удаляет этап и наоборот — `kinds` в `get_topic`."""
    client, _ = admin_client
    stage = _stage(client, db)
    assert client.post(f"{PERIODS_PAGE}/{stage.id}/delete").status_code == 404
    assert client.post(f"{STAGES_PAGE}/{stage.parent_id}/delete").status_code == 404
    assert client.post(f"{STAGES_PAGE}/999999/delete").status_code == 404


def test_student_cannot_delete_period(auth_client, db):
    client, _ = auth_client
    period = LearningTopic(
        title="Чужой", kind=TOPIC_KIND_PERIOD, opens_at=_now(), assign_to_all=True,
    )
    db.add(period)
    db.commit()
    resp = client.post(f"{PERIODS_PAGE}/{period.id}/delete", follow_redirects=False)
    assert resp.status_code in (302, 403)
    db.expire_all()
    assert db.get(LearningTopic, period.id) is not None


def test_delete_button_only_on_empty_cards(admin_client, db):
    client, _ = admin_client
    full = _period(client, db, title="С этапом")
    empty = _period(client, db, title="Пустой")
    stage = _stage(client, db, period_id=full.id, title="С циклом")
    _cycle(client, db, stage_id=stage.id, offset=1)
    lonely = _stage(client, db, period_id=full.id, title="Без циклов")

    def card(html, topic_id):
        start = html.index(f'data-stage-id="{topic_id}"')
        end = html.find("</article>", start)
        return html[start:end]

    periods = client.get(PERIODS_PAGE).text
    assert "data-stage-delete" not in card(periods, full.id)
    assert "data-stage-delete" in card(periods, empty.id)

    stages = client.get(STAGES_PAGE).text
    assert "data-stage-delete" not in card(stages, stage.id)
    assert "data-stage-delete" in card(stages, lonely.id)


# ── проваливание вглубь (владелец 06.10.2026) ────────────────────────────────
# «Проваливаемся в периоды — показываем только этапы этого периода, в этапы —
# только его циклы, в цикл — только его задания.»

def _tab_href(html, label):
    import re
    match = re.search(rf'<a class="prg-tab[^"]*"\s+href="([^"]+)"[^>]*>{label}</a>', html)
    return match.group(1) if match else None


def test_tabs_inside_period_lead_into_period(admin_client, db):
    client, _ = admin_client
    period = _period(client, db)
    stage = _stage(client, db, period_id=period.id, title="Этап А")
    other = _stage(client, db, title="Чужой этап")
    inside = _cycle(client, db, stage_id=stage.id, offset=1)
    outside = _cycle(client, db, stage_id=other.id, offset=10)

    page = client.get(f"{STAGES_PAGE}?period={period.id}").text
    assert _tab_href(page, "Этапы") == f"{STAGES_PAGE}?period={period.id}"
    assert _tab_href(page, "Циклы") == f"{CYCLES_PAGE}?period={period.id}"

    cycles = client.get(f"{CYCLES_PAGE}?period={period.id}").text
    assert f'data-cycle-id="{inside.id}"' in cycles
    assert f'data-cycle-id="{outside.id}"' not in cycles
    # В выборе этапа нового цикла — только этапы периода.
    assert f'<option value="{stage.id}"' in cycles
    assert f'<option value="{other.id}"' not in cycles


def test_tabs_inside_stage_lead_into_stage(admin_client, db):
    client, _ = admin_client
    period = _period(client, db)
    stage = _stage(client, db, period_id=period.id, title="Этап А")
    cycle = _cycle(client, db, stage_id=stage.id, offset=1)

    for url in (f"{CYCLES_PAGE}?stage={stage.id}", f"{CYCLES_PAGE}/{cycle.id}"):
        page = client.get(url).text
        assert _tab_href(page, "Этапы") == f"{STAGES_PAGE}?period={period.id}", url
        assert _tab_href(page, "Циклы") == f"{CYCLES_PAGE}?stage={stage.id}", url


def test_cycle_page_shows_full_path(admin_client, db):
    client, _ = admin_client
    period = _period(client, db, title="1 семестр")
    stage = _stage(client, db, period_id=period.id, title="Октябрь")
    cycle = _cycle(client, db, stage_id=stage.id, offset=1)

    page = client.get(f"{CYCLES_PAGE}/{cycle.id}").text
    assert (
        f'<a href="{PERIODS_PAGE}">Периоды</a> ›'
        f' <a href="{STAGES_PAGE}?period={period.id}">1 семестр</a> ›'
        f' <a href="{CYCLES_PAGE}?stage={stage.id}">Октябрь</a> ›'
    ) in " ".join(page.split())
    assert '<span aria-current="page">Цикл 1</span>' in page


def test_flat_tabs_without_context_stay_flat(admin_client, db):
    client, _ = admin_client
    _stage(client, db)
    page = client.get(STAGES_PAGE).text
    assert _tab_href(page, "Этапы") == STAGES_PAGE
    assert _tab_href(page, "Циклы") == CYCLES_PAGE
    assert "data-program-path" not in page
