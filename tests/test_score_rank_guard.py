"""Балл за работу ученика ставит только Главный преподаватель и выше.

Владелец 30.09.2026: «куратор не может оценивать работу ученика… только дать
обратную связь… но он должен видеть оценку, которую поставит главный
преподаватель», и уточнение — не только пробники, а домашка, контрольная и
любое задание, где ученик отправляет работу. Правило живёт в одном месте
(`rbac.SCORE_MIN_RANK`, зависимость `require_scorer`).

Этот файл — сторож на будущее: новый эндпоинт балла под `require_curator`
(новый тип задания, новый экран проверки) уронит тест, и правило не
отвалится молча, как оно однажды уже поменялось в коде без записи в
инвариантах (01.09.2026).
"""
import re

import pytest
from fastapi.routing import APIRoute

from app.main import app

# Не адреса балла, но тоже решение «оценено — закрыто» (владелец 30.09.2026:
# цикл закрывает только ГП).
_EXTRA_SCORER_ONLY = (
    "/cabinet/feedback/{cycle_id}/close",
)


def _scorer_only_routes() -> list[str]:
    paths = set(_EXTRA_SCORER_ONLY)
    for route in app.routes:
        if not isinstance(route, APIRoute) or "POST" not in route.methods:
            continue
        if "score" in route.path:
            paths.add(route.path)
    return sorted(paths)


def test_guard_sees_known_score_routes():
    """Сторож не пустой: иначе он молча проходил бы на любом коде."""
    paths = _scorer_only_routes()
    assert "/cabinet/students/{student_id}/works/{work_id}/score" in paths
    assert "/cabinet/staff/task-block-submissions/{submission_id}/score" in paths


@pytest.mark.parametrize("path", _scorer_only_routes())
def test_curator_is_refused_on_every_score_route(path, client, user_factory, session_factory):
    curator = user_factory(vk_id=995_001, name="Куратор", role_name="куратор")
    client.cookies.set("session_id", session_factory(curator).id)

    url = re.sub(r"\{[^}]+\}", "1", path)
    resp = client.post(url, data={"score": "80"}, follow_redirects=False)

    assert resp.status_code == 403, f"{path}: куратор не должен ставить балл"


def test_chief_teacher_passes_the_rank_check(client, user_factory, session_factory):
    """Обратная сторона: ГП до эндпоинта доходит (404 — работы нет, но не 403)."""
    chief = user_factory(vk_id=995_002, name="Главный", role_name="админ")
    client.cookies.set("session_id", session_factory(chief).id)

    resp = client.post(
        "/cabinet/staff/task-block-submissions/999999/score", json={"score": 80},
    )
    assert resp.status_code == 404
