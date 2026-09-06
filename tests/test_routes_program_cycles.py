"""Экран циклов программы — период вокруг дней (владелец 06.09.2026).

«Открываем, устанавливаем, с какого по какое это будет цикл — то есть он три
недели». До 06.09.2026 экрана, на котором человек заводит рамку с периодом, в
проекте не было вовсе: эндпоинты жили в админке видео и их не звал ни один
шаблон.

План — plans/2026-09-06-apparchi-block-feed-replaces-week-tabs.md, этап 3.
"""
from datetime import timedelta

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.services.program import msk_date
from app.services.tz import today_msk

PAGE = "/cabinet/staff/program/cycles"


def _payload(**over):
    data = {
        "title": "Предобучение",
        "description": "Три недели до старта",
        "starts_on": (today_msk() + timedelta(days=1)).isoformat(),
        "ends_on": (today_msk() + timedelta(days=15)).isoformat(),
        "is_published": True,
    }
    data.update(over)
    return data


def _cycles(db):
    return db.query(LearningTopic).filter(LearningTopic.kind == TOPIC_KIND_WEEK).all()


# ── доступ ──────────────────────────────────────────────────────────────────

def test_student_cannot_open_cycles(auth_client):
    client, _ = auth_client
    assert client.get(PAGE, follow_redirects=False).status_code in (302, 403)


def test_admin_opens_cycles(admin_client):
    client, _ = admin_client
    resp = client.get(PAGE)
    assert resp.status_code == 200
    assert "Циклы программы" in resp.text


# ── создание ────────────────────────────────────────────────────────────────

def test_admin_creates_cycle_with_period(admin_client, db):
    client, _ = admin_client
    resp = client.post(PAGE, json=_payload())
    assert resp.status_code == 200

    cycles = _cycles(db)
    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle.title == "Предобучение"
    assert cycle.ends_at is not None
    assert msk_date(cycle.opens_at) == today_msk() + timedelta(days=1)
    assert msk_date(cycle.ends_at) == today_msk() + timedelta(days=15)
    assert cycle.is_published is True


def test_end_before_start_is_rejected(admin_client, db):
    """Период задом наперёд — ошибка формы, а не молча схлопнутый цикл."""
    client, _ = admin_client
    resp = client.post(
        PAGE,
        json=_payload(
            starts_on=(today_msk() + timedelta(days=10)).isoformat(),
            ends_on=today_msk().isoformat(),
        ),
    )
    assert resp.status_code == 422
    assert _cycles(db) == []


def test_unpublished_cycle_is_hidden_from_students(admin_client, db):
    client, _ = admin_client
    client.post(PAGE, json=_payload(is_published=False))

    assert _cycles(db)[0].is_published is False


# ── правка ──────────────────────────────────────────────────────────────────

def test_admin_edits_cycle_period(admin_client, db):
    client, _ = admin_client
    client.post(PAGE, json=_payload())
    cycle_id = _cycles(db)[0].id

    resp = client.post(
        f"{PAGE}/{cycle_id}",
        json=_payload(title="Первый месяц",
                      ends_on=(today_msk() + timedelta(days=30)).isoformat()),
    )
    assert resp.status_code == 200

    db.expire_all()
    cycle = db.get(LearningTopic, cycle_id)
    assert cycle.title == "Первый месяц"
    assert msk_date(cycle.ends_at) == today_msk() + timedelta(days=30)


def test_editing_a_missing_cycle_gives_404(admin_client):
    client, _ = admin_client
    resp = client.post(f"{PAGE}/999999", json=_payload())
    assert resp.status_code == 404


def test_cycles_page_lists_existing(admin_client, db):
    client, _ = admin_client
    client.post(PAGE, json=_payload(title="Предобучение"))

    resp = client.get(PAGE)
    assert "Предобучение" in resp.text


def test_only_one_tab_is_active_on_cycles_page(admin_client):
    """«Циклы» — вложенный путь внутри «Календаря»: без явной оговорки в
    шапке подсвечивались бы обе вкладки сразу."""
    client, _ = admin_client
    resp = client.get(PAGE)
    assert resp.text.count("prg-tab is-active") == 1
