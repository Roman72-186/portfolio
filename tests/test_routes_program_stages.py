"""Экран этапов программы (владелец 24.09.2026): Этап → Цикл → Задание.

Этап группирует несколько циклов подряд («месяц», «Предобучение»). Отдельной
сущности под этап нет — это `LearningTopic(kind='stage')`, тот же приём, что
уже применён к циклу (`test_routes_program_cycles.py`).
"""
import re
from datetime import timedelta

from app.models.learning_topic import TOPIC_KIND_STAGE, TOPIC_KIND_WEEK, LearningTopic
from app.services.tracker import cycle_label, stage_cycle_ordinal
from app.services.tz import today_msk

STAGES_PAGE = "/cabinet/staff/program/stages"
CYCLES_PAGE = "/cabinet/staff/program/cycles"


def _payload(**over):
    data = {
        "title": "Предобучение",
        "description": "Первый этап курса",
        "starts_on": (today_msk() + timedelta(days=1)).isoformat(),
        "ends_on": (today_msk() + timedelta(days=30)).isoformat(),
        "is_published": True,
    }
    data.update(over)
    return data


def _stages(db):
    return db.query(LearningTopic).filter(LearningTopic.kind == TOPIC_KIND_STAGE).all()


def _cycles(db):
    return db.query(LearningTopic).filter(LearningTopic.kind == TOPIC_KIND_WEEK).all()


# ── доступ ──────────────────────────────────────────────────────────────────

def test_student_cannot_open_stages(auth_client):
    client, _ = auth_client
    assert client.get(STAGES_PAGE, follow_redirects=False).status_code in (302, 403)


def test_admin_opens_stages(admin_client):
    client, _ = admin_client
    resp = client.get(STAGES_PAGE)
    assert resp.status_code == 200
    assert "Этапы программы" in resp.text


# ── создание этапа ──────────────────────────────────────────────────────────

def test_admin_creates_stage_with_period(admin_client, db):
    client, _ = admin_client
    resp = client.post(STAGES_PAGE, json=_payload())
    assert resp.status_code == 200

    stages = _stages(db)
    assert len(stages) == 1
    stage = stages[0]
    assert stage.title == "Предобучение"
    assert stage.kind == TOPIC_KIND_STAGE
    assert stage.parent_id is None
    assert stage.is_published is True


def test_stage_end_before_start_is_rejected(admin_client, db):
    client, _ = admin_client
    resp = client.post(
        STAGES_PAGE,
        json=_payload(
            starts_on=(today_msk() + timedelta(days=10)).isoformat(),
            ends_on=today_msk().isoformat(),
        ),
    )
    assert resp.status_code == 422
    assert _stages(db) == []


def test_admin_edits_stage(admin_client, db):
    client, _ = admin_client
    client.post(STAGES_PAGE, json=_payload())
    stage = _stages(db)[0]

    resp = client.post(
        f"{STAGES_PAGE}/{stage.id}",
        json=_payload(title="Основной курс", is_published=False),
    )
    assert resp.status_code == 200

    db.refresh(stage)
    assert stage.title == "Основной курс"
    assert stage.is_published is False


def test_editing_missing_stage_404(admin_client):
    client, _ = admin_client
    resp = client.post(f"{STAGES_PAGE}/999999", json=_payload())
    assert resp.status_code == 404


# ── привязка цикла к этапу ───────────────────────────────────────────────────

def test_cycle_can_be_linked_to_stage(admin_client, db):
    client, _ = admin_client
    client.post(STAGES_PAGE, json=_payload())
    stage = _stages(db)[0]

    resp = client.post(
        CYCLES_PAGE,
        json={
            "title": "",
            "description": None,
            "starts_on": (today_msk() + timedelta(days=1)).isoformat(),
            "ends_on": (today_msk() + timedelta(days=7)).isoformat(),
            "is_published": True,
            "stage_id": stage.id,
        },
    )
    assert resp.status_code == 200

    cycle = _cycles(db)[0]
    assert cycle.parent_id == stage.id


def test_cycle_with_missing_stage_id_is_rejected(admin_client, db):
    client, _ = admin_client
    resp = client.post(
        CYCLES_PAGE,
        json={
            "title": "Цикл",
            "description": None,
            "starts_on": (today_msk() + timedelta(days=1)).isoformat(),
            "ends_on": (today_msk() + timedelta(days=7)).isoformat(),
            "is_published": True,
            "stage_id": 999999,
        },
    )
    assert resp.status_code == 422
    assert _cycles(db) == []


def test_week_topic_list_does_not_leak_stage(admin_client, db):
    """`/cycles` не должен показывать этап карточкой в списке циклов —
    только в выпадающем списке формы, где он и должен быть виден."""
    client, _ = admin_client
    client.post(STAGES_PAGE, json=_payload())
    stage = _stages(db)[0]

    resp = client.get(CYCLES_PAGE)
    assert resp.status_code == 200
    assert f'data-cycle-id="{stage.id}"' not in resp.text


def test_stage_cycle_ordinal_labels_unnamed_cycles(admin_client, db):
    """Пустой title + есть этап → «Цикл N», по порядку начала периода."""
    client, _ = admin_client
    client.post(STAGES_PAGE, json=_payload())
    stage = _stages(db)[0]

    for offset in (1, 8, 15):
        client.post(
            CYCLES_PAGE,
            json={
                "title": "",
                "description": None,
                "starts_on": (today_msk() + timedelta(days=offset)).isoformat(),
                "ends_on": (today_msk() + timedelta(days=offset + 6)).isoformat(),
                "is_published": True,
                "stage_id": stage.id,
            },
        )

    cycles = sorted(_cycles(db), key=lambda c: c.opens_at)
    assert [stage_cycle_ordinal(db, c) for c in cycles] == [1, 2, 3]
    assert cycle_label(db, cycles[1]) == "Цикл 2"


def test_legacy_cycle_without_stage_keeps_date_label(admin_client, db):
    """Цикл без этапа (легаси) — подпись по-прежнему период дат, не «Цикл N»."""
    client, _ = admin_client
    client.post(
        CYCLES_PAGE,
        json={
            "title": "",
            "description": None,
            "starts_on": (today_msk() + timedelta(days=1)).isoformat(),
            "ends_on": (today_msk() + timedelta(days=7)).isoformat(),
            "is_published": True,
        },
    )
    cycle = _cycles(db)[0]
    assert cycle.parent_id is None
    label = cycle_label(db, cycle)
    assert "Цикл " not in label or "–" in label
    assert "." in label


# ── переключатель циклов плашками (владелец 24.09.2026) ────────────────────

def _linked_cycle(client, db, *, stage_id, offset):
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
    return sorted(_cycles(db), key=lambda c: c.opens_at)[-1]


def test_cycle_page_shows_tiles_for_stage_siblings(admin_client, db):
    client, _ = admin_client
    client.post(STAGES_PAGE, json=_payload())
    stage = _stages(db)[0]
    cycle_1 = _linked_cycle(client, db, stage_id=stage.id, offset=1)
    cycle_2 = _linked_cycle(client, db, stage_id=stage.id, offset=8)

    resp = client.get(f"{CYCLES_PAGE}/{cycle_1.id}")

    assert resp.status_code == 200
    tiles = re.findall(
        r'<a class="prg-cycle-pill( is-active)?"\s+href="/cabinet/staff/program/cycles/(\d+)">',
        resp.text,
    )
    tiles_by_id = {int(cycle_id): bool(active) for active, cycle_id in tiles}
    assert tiles_by_id == {cycle_1.id: True, cycle_2.id: False}


def test_cycle_page_hides_tiles_when_alone_in_stage(admin_client, db):
    client, _ = admin_client
    client.post(STAGES_PAGE, json=_payload())
    stage = _stages(db)[0]
    cycle = _linked_cycle(client, db, stage_id=stage.id, offset=1)

    resp = client.get(f"{CYCLES_PAGE}/{cycle.id}")

    assert resp.status_code == 200
    # Переключатель не рисуется вовсе, когда переключать не на что — «Этап
    # «...»» появляется только вместе с ним.
    assert "Этап «" not in resp.text


def test_cycle_page_hides_tiles_for_legacy_cycle_without_stage(admin_client, db):
    client, _ = admin_client
    client.post(
        CYCLES_PAGE,
        json={
            "title": "",
            "description": None,
            "starts_on": (today_msk() + timedelta(days=1)).isoformat(),
            "ends_on": (today_msk() + timedelta(days=7)).isoformat(),
            "is_published": True,
        },
    )
    cycle = _cycles(db)[0]
    assert cycle.parent_id is None

    resp = client.get(f"{CYCLES_PAGE}/{cycle.id}")

    assert resp.status_code == 200
    assert "Этап «" not in resp.text
