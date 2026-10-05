"""Карточка ученика показывает то, с чем ученик работает сейчас (владелец 29.09.2026).

Снято: отработки (у ученика к ним нет входа с мая 2026), VK, фильтры по окну
сдачи пробника, отдельная вкладка «Цикл пробника» (слита с «Пробниками»).
Добавлено: блок «Учёба сейчас» в профиле и вкладка «Задания» — ответы и сдачи
из ленты. С 05.10.2026 проверяют во вкладке «Задания» (экран «Проверка по
ученику» снят) — её кнопки сторожит `test_routes_student_review.py`.
"""

import pathlib
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from app.models.task_block import BLOCK_QUESTION, BLOCK_UPLOAD, TaskBlock, TaskBlockSubmission
from app.models.tracker import TrackerTask
from app.models.work import WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE, Work
from app.services.task_blocks import save_response, sync_blocks
from app.services.user_management import archive_user


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _chief(user_factory, vk_id=950_001):
    return user_factory(vk_id=vk_id, name="Главный", is_admin=True, role_name="админ")


def _task(db, title="Свет и тень", subject="Рисунок") -> TrackerTask:
    task = TrackerTask(title=title, kind="material", subject=subject)
    db.add(task)
    db.flush()
    return task


def _answer(db, student, question="Что было главным?", text="Тон"):
    task = _task(db)
    [block] = sync_blocks(
        db, task_id=task.id, items=[{"block_type": BLOCK_QUESTION, "body": question}],
    )
    db.commit()
    save_response(
        db, task_id=task.id, user_id=student.id, blocks=[block],
        answers={block.id: {"text": text}},
    )
    db.commit()


def _block_work(db, student, title="Сдай листы"):
    task = _task(db, title=title)
    block = TaskBlock(task_id=task.id, block_type=BLOCK_UPLOAD, title=title)
    db.add(block)
    db.flush()
    db.add(TaskBlockSubmission(
        block_id=block.id, user_id=student.id, submitted_at=datetime.now(timezone.utc),
    ))
    db.commit()


def _mock(db, student, *, created_at=None, work_type=WORK_TYPE_MOCK_EXAM):
    work = Work(
        user_id=student.id, work_type=work_type, month="сентябрь", year=2026,
        filename="final.jpg", s3_url="https://s3.example.com/final.jpg",
        subject="Рисунок", status="success", is_final=True,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


# ── Вкладка «Задания» ────────────────────────────────────────────────────────

def test_tasks_tab_lists_answers_and_block_works_but_not_mock(
    client, db, user_factory, session_factory
):
    chief = _chief(user_factory)
    student = user_factory(vk_id=950_101, name="Ученик")
    _answer(db, student)
    _block_work(db, student)
    _mock(db, student)
    _login(client, session_factory, chief)

    resp = client.get(f"/cabinet/students/{student.id}/tasks")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {i["domain"] for i in items} == {"task_block", "block_work"}
    answer = next(i for i in items if i["domain"] == "task_block")
    assert answer["question"] == "Что было главным?"
    assert answer["text"] == "Тон"
    assert answer["is_reviewed"] is False
    # Своего экрана у ответа нет — его отмечают прямо во вкладке.
    assert answer["review_url"] == ""
    work = next(i for i in items if i["domain"] == "block_work")
    assert work["review_url"].startswith("/cabinet/staff/task-block-submissions/")


def test_tasks_tab_refuses_curator_foreign_student(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=950_201, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=950_202, name="Чужой ученик")
    _login(client, session_factory, curator)

    resp = client.get(f"/cabinet/students/{student.id}/tasks")

    assert resp.status_code == 403


def test_tasks_tab_reads_archived_student_for_chief(client, db, user_factory, session_factory):
    chief = _chief(user_factory)
    student = user_factory(vk_id=950_301, name="Выпускник")
    _answer(db, student)
    superadmin = user_factory(vk_id=950_302, name="СА", is_admin=True, role_name="суперадмин")
    assert archive_user(db, target_user_id=student.id, performed_by_id=superadmin.id) is True
    _login(client, session_factory, chief)

    resp = client.get(f"/cabinet/students/{student.id}/tasks")

    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


def test_moderator_reads_tasks_tab(client, db, user_factory, session_factory):
    moderator = user_factory(vk_id=950_401, name="Наблюдатель", role_name="модератор")
    student = user_factory(vk_id=950_402, name="Ученик")
    _answer(db, student)
    _login(client, session_factory, moderator)

    resp = client.get(f"/cabinet/students/{student.id}/tasks")

    assert resp.status_code == 200


# ── Профиль: «Учёба сейчас», без VK и отработок ──────────────────────────────

def test_profile_drops_vk_retake_and_cycle_fields(client, db, user_factory, session_factory):
    chief = _chief(user_factory)
    student = user_factory(vk_id=950_501, name="Ученик")
    _login(client, session_factory, chief)

    data = client.get(f"/cabinet/students/{student.id}/profile").json()["student"]

    for gone in ("vk_id", "vk_profile_url", "is_group_member", "retake_count", "cycle_count"):
        assert gone not in data
    assert data["study_now"]["unreviewed"] == 0


def test_profile_study_now_counts_all_time_and_opens_tasks(
    client, db, user_factory, session_factory
):
    """Счётчик — за всё время, а не за неделю; «Проверить» ведёт на вкладку
    «Задания», раз непроверенное есть и там (05.10.2026)."""
    chief = _chief(user_factory)
    student = user_factory(vk_id=950_601, name="Ученик")
    old = datetime.now(timezone.utc) - timedelta(days=20)
    _mock(db, student, created_at=old)
    _answer(db, student)
    _login(client, session_factory, chief)

    study_now = client.get(f"/cabinet/students/{student.id}/profile").json()["student"]["study_now"]

    assert study_now["unreviewed"] == 2
    assert study_now["review_tab"] == "tasks"
    assert "point_a" in study_now


def test_profile_hides_point_a_from_curator(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=950_701, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=950_702, name="Свой ученик")
    student.curator_id = curator.id
    db.commit()
    _login(client, session_factory, curator)

    study_now = client.get(f"/cabinet/students/{student.id}/profile").json()["student"]["study_now"]

    assert "point_a" not in study_now


# ── Страница «Ученики» ───────────────────────────────────────────────────────

def test_students_page_has_tasks_tab_and_no_retired_mechanics(
    client, db, user_factory, session_factory
):
    chief = _chief(user_factory)
    user_factory(vk_id=950_801, name="Ученик")
    _login(client, session_factory, chief)

    page = client.get("/cabinet/students").text

    assert 'id="tab-tasks"' in page
    assert 'id="tab-cycles"' not in page
    assert '<option value="retake">' not in page
    assert "has_unchecked_mocks" not in page
    assert "mock_period_submitted" not in page
    assert "period_only" not in page
    assert "data-vk-id" not in page


def test_old_cycles_tab_link_opens_mock_exams(client, db, user_factory, session_factory):
    chief = _chief(user_factory)
    student = user_factory(vk_id=950_901, name="Ученик")
    _login(client, session_factory, chief)

    page = client.get(f"/cabinet/students?student={student.id}&tab=cycles").text

    assert 'const INITIAL_TAB    = "mock-exams";' in page


# ── Отработки сняты ──────────────────────────────────────────────────────────

def test_retake_routes_are_gone(client, db, user_factory, session_factory):
    chief = _chief(user_factory)
    student = user_factory(vk_id=951_001, name="Ученик")
    work = _mock(db, student)
    _login(client, session_factory, chief)

    assert client.get(f"/cabinet/students/{student.id}/retakes").status_code == 404
    assert client.get(f"/cabinet/students/{student.id}/cycles").status_code == 404
    assert client.post(
        f"/cabinet/students/{student.id}/mock-exams/{work.id}/retake",
        data={"score": "40", "comment": "x"},
    ).status_code in (404, 405)


def test_staff_upload_refuses_retake(client, db, user_factory, session_factory):
    chief = _chief(user_factory)
    student = user_factory(vk_id=951_101, name="Ученик")
    _login(client, session_factory, chief)

    resp = client.post(
        f"/cabinet/students/{student.id}/upload",
        data={"work_type": "retake", "month": "сентябрь", "year": "2026", "subject": "Рисунок"},
        files={"photos": ("a.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )

    assert resp.status_code == 400
    assert resp.json()["error"] == "Неверный тип работы"


def test_review_queue_skips_retakes(db, user_factory):
    from app.services.review_aggregate import student_review_items

    student = user_factory(vk_id=951_201, name="Ученик")
    _mock(db, student, work_type=WORK_TYPE_RETAKE)

    assert student_review_items(db, student_id=student.id, role_rank=4) == []


# ── Скрипт страницы: правило 11 ──────────────────────────────────────────────

KNOWN_GLOBALS = {
    "if", "for", "while", "switch", "catch", "function", "return", "typeof",
    "new", "in", "of", "do", "else", "try", "throw", "delete", "void",
    "Array", "Object", "JSON", "String", "Number", "Boolean", "Promise",
    "Date", "Math", "Set", "Map", "RegExp", "Error", "FormData", "URL",
    "URLSearchParams", "Blob", "File", "FileReader", "IntersectionObserver",
    "MutationObserver", "CustomEvent", "Event", "XMLHttpRequest", "DataTransfer",
    "fetch", "parseInt", "parseFloat", "isNaN", "isFinite", "encodeURIComponent",
    "decodeURIComponent", "setTimeout", "clearTimeout", "setInterval",
    "clearInterval", "requestAnimationFrame", "alert", "confirm", "prompt",
    "console", "queueMicrotask", "structuredClone", "AbortController",
}


def _strip_noise(js: str) -> str:
    # Регулярки вида `.replace(/"/g, …)`: кавычка внутри них иначе читается
    # как начало строки и съедает объявления функций до следующей кавычки.
    js = re.sub(r"\((/(?:\\.|[^/\\\n])+/[gimsuy]*)", "(RE", js)
    js = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
    js = re.sub(r"(?m)^\s*//.*$", " ", js)
    js = re.sub(r"//[^\n'\"]*$", " ", js, flags=re.M)
    js = re.sub(r"'(?:\\.|[^'\\])*'", "''", js)
    js = re.sub(r'"(?:\\.|[^"\\])*"', '""', js)
    return js


def _students_page_scripts(client, db, user_factory, session_factory) -> list[str]:
    chief = _chief(user_factory)
    user_factory(vk_id=951_301, name="Ученик")
    _login(client, session_factory, chief)
    page = client.get("/cabinet/students")
    assert page.status_code == 200
    # Встроенные скрипты с атрибутами тоже: календарь пробника
    # (`partials/cycle_calendar_lib.html`) объявляет свои функции в таком.
    scripts = [
        body for body in re.findall(r"<script\b[^>]*>(.*?)</script>", page.text, re.S)
        if body.strip()
    ]
    assert scripts
    return scripts


def test_students_page_script_calls_only_defined_functions(
    client, db, user_factory, session_factory
):
    """Из `<script>` страницы вырезаны отработки, циклы и «Показать все»:
    между функциями отработок лежали общие функции формы оценки
    (`guardUnsavedScore` и соседи), их легко унести заодно."""
    scripts = [_strip_noise(s) for s in _students_page_scripts(client, db, user_factory, session_factory)]
    declared: set[str] = set()
    for js in scripts:
        declared |= set(re.findall(r"function\s+(\w+)", js))
        declared |= set(re.findall(r"\b(?:var|let|const)\s+(\w+)", js))
        declared |= set(re.findall(r"\b(\w+)\s*=\s*function", js))
        for params in re.findall(r"function[^(]*\(([^)]*)\)", js):
            declared |= {p.strip() for p in params.split(",") if p.strip()}
    called: set[str] = set()
    for js in scripts:
        called |= set(re.findall(r"(?<![.\w$])([A-Za-z_$]\w*)\s*\(", js))

    missing = sorted(called - declared - KNOWN_GLOBALS)

    assert not missing, f"вызовы без определения: {missing}"


def test_students_page_scripts_are_valid_javascript(
    client, db, user_factory, session_factory
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js не установлен — синтаксис скрипта не проверить")
    scripts = _students_page_scripts(client, db, user_factory, session_factory)
    with tempfile.TemporaryDirectory() as tmp:
        for number, raw in enumerate(scripts):
            path = pathlib.Path(tmp) / f"students_script_{number}.js"
            path.write_text(raw, encoding="utf-8")
            check = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
            assert check.returncode == 0, f"скрипт #{number} не разбирается: " + check.stderr
