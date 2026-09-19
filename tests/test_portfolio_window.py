"""Окно загрузки портфолио: сроки держит блок «Загрузить портфолио».

Владелец 18.09.2026: «Есть открытое окно — может загружать и удалять фото из
ДО, нет открытого окна — уже не может ни загрузить, ни удалить». Правка
родилась из вала обращений, который владелец ждал после 18-19 сентября: «я не
загрузил, я ошибся, можете поправить» — исправление переезжает к самому
ученику, но ровно на срок окна.

Здесь же сторож обратной совместимости: у групп без блока портфолио загрузка
обязана работать как раньше, иначе новое правило молча отрезало бы им
портфолио целиком.
"""
from datetime import timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.task_block import BLOCK_PORTFOLIO, TaskBlock, TaskBlockState
from app.models.user import User
from app.models.work import Work
from app.services.cycle_feed import build_cycle_feed
from app.services.program import day_bounds
from app.services.tracker import create_task
from app.services.tz import now_msk, today_msk

pytestmark = pytest.mark.usefixtures("enable_n8n")

_MOCK_N8N = "app.api.upload.send_photo_to_n8n"
_OK_RESULT = {"success": True, "drive_file_id": "gdrive_abc"}
_JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16

TODAY = today_msk()


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _task(db, owner, title="День портфолио"):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _portfolio_block(
    db, task, *, opens_at=None, closes_at=None, window_hours=None
):
    block = TaskBlock(
        task_id=task.id, block_type=BLOCK_PORTFOLIO,
        title="Загрузите портфолио", sort_order=1, is_required=True,
        opens_at=opens_at, closes_at=closes_at,
        portfolio_window_hours=window_hours,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _open_window(db, user, *, closes_in_days=1):
    """Блок с окном, которое закроется через сутки."""
    task = _task(db, user)
    return _portfolio_block(
        db, task,
        opens_at=_utc(now_msk() - timedelta(days=1)),
        closes_at=_utc(now_msk() + timedelta(days=closes_in_days)),
    )


def _closed_window(db, user):
    task = _task(db, user)
    return _portfolio_block(
        db, task,
        opens_at=_utc(now_msk() - timedelta(days=3)),
        closes_at=_utc(now_msk() - timedelta(days=1)),
    )


def _work(db, user, *, work_type="before", status="success", **fields):
    work = Work(
        user_id=user.id, work_type=work_type, month="сентябрь", year=TODAY.year,
        filename="work.jpg", s3_url="https://example.com/work.jpg",
        s3_path=None, status=status, **fields,
    )
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


def _csrf(client):
    """Токен берём со страницы загрузки — тем же путём, что и браузер."""
    import re

    page = client.get("/upload?section=before").text
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match, "на странице загрузки нет csrf-токена"
    return match.group(1)


# ── сторож обратной совместимости ───────────────────────────────────────────

def test_upload_stays_open_when_no_portfolio_block_exists(auth_client, db):
    """Блока портфолио нет — загрузка работает как раньше.

    Сроки задаёт блок, и там, где его никто не заводил, запирать нечего:
    ученики старых групп не должны потерять портфолио из-за правила, которое
    писали под поток предобучения.
    """
    client, _ = auth_client

    resp = client.get("/upload?section=before")

    assert resp.status_code == 200
    assert "Загрузка закрыта" not in resp.text


# ── закрытое окно ───────────────────────────────────────────────────────────

def test_closed_window_hides_the_form_and_names_the_deadline(auth_client, db):
    client, user = auth_client
    _closed_window(db, user)

    resp = client.get("/upload?section=before")

    assert resp.status_code == 200
    assert "Загрузка закрыта" in resp.text
    assert "Работы принимали до" in resp.text
    assert 'id="photoInput"' not in resp.text


def test_future_window_is_closed_too(auth_client, db):
    """Окно ещё не открылось — грузить нельзя так же, как после закрытия."""
    client, user = auth_client
    task = _task(db, user)
    _portfolio_block(db, task, opens_at=_utc(now_msk() + timedelta(days=2)))

    resp = client.get("/upload?section=before")

    assert resp.status_code == 200
    assert "Загрузка закрыта" in resp.text


def test_closed_window_rejects_the_json_upload(auth_client, db):
    """Старая вкладка не должна обходить срок: отправка идёт через XHR."""
    client, user = auth_client
    _closed_window(db, user)

    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = client.post(
            "/upload/api",
            data={"section": "before"},
            files=[("photos", ("p.jpg", _JPG_BYTES, "image/jpeg"))],
        )

    assert resp.status_code == 422
    assert resp.json()["success"] is False
    assert db.query(Work).count() == 0


def test_closed_window_rejects_the_form_upload(auth_client, db):
    client, user = auth_client
    _closed_window(db, user)

    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = client.post(
            "/upload",
            data={"section": "before"},
            files=[("photos", ("p.jpg", _JPG_BYTES, "image/jpeg"))],
        )

    assert resp.status_code == 200
    assert "Загрузка закрыта" in resp.text
    assert db.query(Work).count() == 0


# ── открытое окно ───────────────────────────────────────────────────────────

def test_open_window_shows_the_form_without_deadline_in_before(auth_client, db):
    client, user = auth_client
    _open_window(db, user)

    resp = client.get("/upload?section=before")

    assert resp.status_code == 200
    assert 'id="photoInput"' in resp.text
    assert "Загрузить и заменить работы можно до" not in resp.text


def test_personal_window_starts_once_and_uses_configured_hours(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _portfolio_block(
        db,
        task,
        window_hours=48,
        # Персональная длительность заменяет старую общую дату закрытия.
        closes_at=_utc(now_msk() - timedelta(days=1)),
    )
    build_cycle_feed(
        db,
        user_id=user.id,
        user_tariff=user.tariff,
        start=TODAY,
        end=TODAY,
    )

    first = client.get(f"/upload?section=before&block={block.id}")
    state = db.query(TaskBlockState).filter_by(
        block_id=block.id, user_id=user.id
    ).one()
    started_at = state.started_at
    second = client.get(f"/upload?section=before&block={block.id}")
    db.refresh(state)

    assert first.status_code == 200
    assert 'id="photoInput"' in first.text
    assert "Загрузить и заменить работы можно до" not in first.text
    assert second.status_code == 200
    assert state.started_at == started_at


def test_personal_window_closes_after_its_own_deadline(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _portfolio_block(db, task, window_hours=24)
    build_cycle_feed(
        db,
        user_id=user.id,
        user_tariff=user.tariff,
        start=TODAY,
        end=TODAY,
    )
    state = db.query(TaskBlockState).filter_by(
        block_id=block.id, user_id=user.id
    ).one()
    state.started_at = _utc(now_msk() - timedelta(hours=25))
    db.commit()

    resp = client.get(f"/upload?section=before&block={block.id}")

    assert resp.status_code == 200
    assert "Загрузка закрыта" in resp.text
    assert 'id="photoInput"' not in resp.text


def test_open_window_shows_already_uploaded_works(auth_client, db):
    client, user = auth_client
    _open_window(db, user)
    work = _work(db, user)

    resp = client.get("/upload?section=before")

    assert "Уже загружено" in resp.text
    assert f'data-delete-work="{work.id}"' in resp.text


def test_reviewed_work_has_no_delete_button(auth_client, db):
    client, user = auth_client
    _open_window(db, user)
    work = _work(db, user, viewed_at=now_msk())

    resp = client.get("/upload?section=before")

    assert f'data-work-id="{work.id}"' in resp.text
    assert f'data-delete-work="{work.id}"' not in resp.text


# ── удаление своих работ ────────────────────────────────────────────────────

def test_student_deletes_own_work_inside_the_window(auth_client, db):
    client, user = auth_client
    _open_window(db, user)
    work = _work(db, user)
    token = _csrf(client)

    resp = client.delete(
        f"/upload/works/{work.id}", headers={"X-CSRF-Token": token}
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert db.query(Work).filter(Work.id == work.id).first() is None


def test_delete_route_is_csrf_protected():
    """Сторож на зависимость, а не на ответ: `conftest` глушит CSRF заглушкой
    на весь набор, поэтому запросом защиту здесь не проверить. Сама зависимость
    у роута обязана стоять — иначе чужая страница удалит работы за ученика.
    """
    from app.dependencies import require_csrf_header
    from app.main import app

    route = next(
        r for r in app.routes
        if getattr(r, "path", "") == "/upload/works/{work_id}"
    )
    checks = [d.call for d in route.dependant.dependencies]
    assert require_csrf_header in checks


def test_delete_is_refused_after_the_window_closed(auth_client, db):
    client, user = auth_client
    block = _open_window(db, user)
    work = _work(db, user)
    token = _csrf(client)
    block.closes_at = _utc(now_msk() - timedelta(hours=1))
    db.commit()

    resp = client.delete(
        f"/upload/works/{work.id}", headers={"X-CSRF-Token": token}
    )

    assert resp.status_code == 422
    assert "закрыт" in resp.json()["error"]
    assert db.query(Work).filter(Work.id == work.id).first() is not None


def test_reviewed_work_is_not_deletable(auth_client, db):
    """Оценка и комментарий куратора не должны остаться без работы."""
    client, user = auth_client
    _open_window(db, user)
    work = _work(db, user, score=80, scored_at=now_msk())
    token = _csrf(client)

    resp = client.delete(
        f"/upload/works/{work.id}", headers={"X-CSRF-Token": token}
    )

    assert resp.status_code == 422
    assert "преподаватель" in resp.json()["error"]
    assert db.query(Work).filter(Work.id == work.id).first() is not None


def test_student_cannot_delete_someone_elses_work(auth_client, db, user_factory):
    client, user = auth_client
    _open_window(db, user)
    stranger = user_factory(vk_id=987654321, name="Чужой ученик")
    work = _work(db, stranger)
    token = _csrf(client)

    resp = client.delete(
        f"/upload/works/{work.id}", headers={"X-CSRF-Token": token}
    )

    assert resp.status_code == 404
    assert db.query(Work).filter(Work.id == work.id).first() is not None


def test_no_window_does_not_grant_the_right_to_delete(auth_client, db):
    """Послабление для групп без блока касается только загрузки.

    Иначе оно выдало бы новое право: раздел «После» окон пока не знает вовсе,
    и ученик сносил бы учебные работы круглый год вместе с файлами хранилища.
    """
    client, user = auth_client
    _open_window(db, user)  # окно есть, но оно «До»
    work = _work(db, user, work_type="after")
    token = _csrf(client)

    resp = client.delete(
        f"/upload/works/{work.id}", headers={"X-CSRF-Token": token}
    )

    assert resp.status_code == 422
    assert db.query(Work).filter(Work.id == work.id).first() is not None


def test_after_section_uploads_any_time_even_when_before_is_closed(auth_client, db):
    """«После» грузится всегда (владелец 18.09.2026: «в портфолио После грузить
    могут в любое время»). Сроки — только про «До», и закрытое окно «До» не
    должно перекрывать учебные работы, которые ученик сдаёт весь год.
    """
    client, user = auth_client
    _closed_window(db, user)

    page = client.get("/upload?section=after")
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        upload = client.post(
            "/upload/api",
            data={"section": "after", "month": "сентябрь"},
            files=[("photos", ("p.jpg", _JPG_BYTES, "image/jpeg"))],
        )

    assert "Загрузка закрыта" not in page.text
    assert upload.status_code == 200
    assert upload.json()["success"] is True
    assert db.query(Work).filter(Work.work_type == "after").count() == 1


def test_after_section_shows_no_delete_buttons(auth_client, db):
    """Экран «После» остаётся прежним: окон для него пока нет."""
    client, user = auth_client
    _work(db, user, work_type="after")

    resp = client.get("/upload?section=after")

    assert resp.status_code == 200
    # Именно кнопки, а не любое упоминание: обработчик и его комментарии живут
    # в скрипте страницы всегда, просто без карточки им не к чему цепляться.
    assert 'data-delete-work="' not in resp.text
    assert 'id="existingGrid"' not in resp.text


def test_mock_exam_work_is_out_of_this_windows_reach(auth_client, db):
    """Пробник сдаётся по своим правилам, окно портфолио его не касается."""
    client, user = auth_client
    _open_window(db, user)
    work = _work(db, user, work_type="mock_exam")
    token = _csrf(client)

    resp = client.delete(
        f"/upload/works/{work.id}", headers={"X-CSRF-Token": token}
    )

    assert resp.status_code == 403
    assert db.query(Work).filter(Work.id == work.id).first() is not None


def test_deleting_every_work_keeps_portfolio_onboarding_done(auth_client, db):
    """Флаг «До сдано» не откатывается: его сброс запер бы ученику портфолио,
    галерею и обратную связь прямо посреди окна (гейт в dependencies.py)."""
    client, user = auth_client
    _open_window(db, user)
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.commit()
    work = _work(db, user)
    token = _csrf(client)

    client.delete(f"/upload/works/{work.id}", headers={"X-CSRF-Token": token})

    db.refresh(user)
    assert user.portfolio_do_completed is True
