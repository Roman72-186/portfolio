"""Экран циклов программы — период вокруг дней (владелец 06.09.2026).

«Открываем, устанавливаем, с какого по какое это будет цикл — то есть он три
недели». До 06.09.2026 экрана, на котором человек заводит рамку с периодом, в
проекте не было вовсе: эндпоинты жили в админке видео и их не звал ни один
шаблон.

План — plans/2026-09-06-apparchi-block-feed-replaces-week-tabs.md, этап 3.
"""
from datetime import timedelta

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.learning_video import LearningVideo
from app.services.cycle_feed import feed_for_student
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


# ── удаление ────────────────────────────────────────────────────────────────

def test_admin_deletes_cycle(admin_client, db):
    """Удаление мягкое: строка остаётся с `deleted_at`, из списка цикл уходит.

    Физического удаления нет намеренно — ошибочный клик отменяется правкой
    одной колонки, а задания внутри цикла сохраняют историю ответов.
    """
    client, _ = admin_client
    client.post(PAGE, json=_payload(title="Предобучение"))
    cycle_id = _cycles(db)[0].id

    resp = client.post(f"{PAGE}/{cycle_id}/delete")
    assert resp.status_code == 200

    db.expire_all()
    cycle = db.get(LearningTopic, cycle_id)
    assert cycle is not None
    assert cycle.deleted_at is not None
    assert cycle.is_published is False
    assert "Предобучение" not in client.get(PAGE).text


def test_deleted_cycle_disappears_from_student_feed(admin_client, db, regular_user):
    """Главное, ради чего кнопка и заводилась: цикл уходит у учеников.

    Задания внутри остаются неудалёнными — ленте они не видны потому, что без
    живого цикла окно падает на календарную неделю, а бездатные задания в неё
    не подмешиваются.
    """
    client, _ = admin_client
    client.post(PAGE, json=_payload(starts_on=today_msk().isoformat()))
    cycle_id = _cycles(db)[0].id
    assert client.post(
        f"{PAGE}/{cycle_id}/items/material", json={"title": "Первое задание"}
    ).status_code == 200

    def steps():
        db.expire_all()
        return feed_for_student(
            db, user_id=regular_user.id, user_tariff=regular_user.tariff,
            today=today_msk(),
        )

    assert [s["task"].title for s in steps()["steps"]] == ["Первое задание"]

    assert client.post(f"{PAGE}/{cycle_id}/delete").status_code == 200

    after = steps()
    assert after["topic"] is None
    assert after["steps"] == []
    assert after["cycles"] == []


def test_cycle_card_warns_about_videos_bound_to_the_topic(admin_client, db):
    """Ролики старой привязки (`LearningVideo.topic_id`) тоже уходят у учеников.

    В счёт заданий они не входят, и без отдельного числа человек увидел бы
    «внутри 0» — а доступ к ролику потерял бы (прецедент гейта в
    `video_admin.py::delete_video_topic`).
    """
    client, _ = admin_client
    client.post(PAGE, json=_payload())
    cycle = _cycles(db)[0]
    db.add(
        LearningVideo(
            bunny_library_id=720058,
            bunny_video_id="guid-cycle-1",
            title="Лекция недели",
            status="ready",
            topic_id=cycle.id,
        )
    )
    db.commit()

    text = client.get(PAGE).text
    assert 'data-items="0"' in text
    assert 'data-videos="1"' in text


def test_student_cannot_delete_cycle(auth_client):
    client, _ = auth_client
    assert client.post(f"{PAGE}/1/delete").status_code in (302, 403)


def test_deleting_a_missing_cycle_gives_404(admin_client):
    client, _ = admin_client
    assert client.post(f"{PAGE}/999999/delete").status_code == 404


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
