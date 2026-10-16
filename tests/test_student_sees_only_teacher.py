"""Ученик видит только «Преподавателя» (владелец 05.10.2026: «ученик НИКОГДА
НЕ ДОЛЖЕН ЗНАТЬ, ЧТО ЕМУ ПИШЕТ КТО-ТО КРОМЕ ПРЕПОДАВАТЕЛЯ»). Инвариант —
`docs/invariants/feedback.md`.

Сторож по исходникам экранов, которые открывает только ученик: ни куратора,
ни Главного преподавателя, ни суперадмина, ни модератора в тексте страницы.
Jinja-комментарии `{# #}` не в счёт — их ученик не видит. Подписи в диалогах
ОС держит `services/feedback.py::dialog_sender` и его тесты."""

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

# Шаблоны ученических роутов (`require_student`) и экран видеоуроков.
# Сдача пробника (`upload_mock.html` и встроенная `partials/inline/mock_exam.html`)
# добавлена 16.10.2026: там нашёлся «проверяющий».
STUDENT_TEMPLATES = [
    "cabinet_cycle.html",
    "cabinet_learning.html",
    "cabinet_learning_archive.html",
    "cabinet_notifications.html",
    "cabinet_personal.html",
    "cabinet_personal_contacts.html",
    "cabinet_portfolio.html",
    "cabinet_student.html",
    "cabinet_tracker.html",
    "cabinet_videos.html",
    "upload_mock.html",
    "partials/inline/mock_exam.html",
]

STAFF_ROLE = re.compile(
    r"куратор|главн\w* преподават|суперадмин|модератор|проверяющ|\bГП\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize("name", STUDENT_TEMPLATES)
def test_student_screen_names_no_staff_role(name):
    source = (TEMPLATES / name).read_text(encoding="utf-8")
    visible = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
    found = sorted({m.group(0) for m in STAFF_ROLE.finditer(visible)})
    assert not found, f"{name}: ученик видит {found} – пишите «преподаватель»"


def _visible_text(html: str) -> str:
    """Текст страницы без скриптов, стилей и HTML-комментариев."""
    html = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->", "", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def test_mock_dialog_signs_staff_as_teacher_for_student(auth_client, db, user_factory):
    """Диалог пробника: сообщения куратора и суперадмина ученик видит
    подписанными «Преподаватель» — без имени и должности (16.10.2026)."""
    from datetime import date

    from app.models.exam_cycle import ExamCycle
    from app.models.feedback import Feedback, FeedbackMessage
    from app.models.work import WORK_TYPE_MOCK_EXAM, Work

    client, student = auth_client
    curator = user_factory(vk_id=200_002, name="Пётр Кураторов", role_name="куратор")
    boss = user_factory(vk_id=200_003, name="Анна Главная", role_name="суперадмин")
    cycle = ExamCycle(user_id=student.id, subject="Drawing", started_at=date(2026, 5, 10))
    db.add(cycle)
    db.flush()
    work = Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="05", year=2026,
        filename="final-1.jpg", subject="Drawing", status="success",
        s3_url="https://example.test/final-1.jpg", is_final=True,
        cycle_id=cycle.id, attempt_number=1,
    )
    db.add(work)
    db.flush()
    fb = Feedback(work_id=work.id, curator_id=curator.id)
    db.add(fb)
    db.flush()
    db.add_all([
        FeedbackMessage(feedback_id=fb.id, sender_id=curator.id,
                        sender_role="curator", text="Разбор от первого"),
        FeedbackMessage(feedback_id=fb.id, sender_id=boss.id,
                        sender_role="superadmin", text="Разбор от второго"),
    ])
    db.commit()

    resp = client.get(f"/cabinet/feedback/{cycle.id}")

    assert resp.status_code == 200
    text = _visible_text(resp.text)
    assert "Разбор от первого" in text and "Разбор от второго" in text
    assert text.count("Преподаватель ·") == 2
    found = sorted({m.group(0) for m in STAFF_ROLE.finditer(text)})
    assert not found, f"ученик видит {found}"
    assert "Кураторов" not in resp.text and "Анна Главная" not in resp.text


# Ученика не отправляют «к администратору» – только в службу заботы
# (владелец 16.10.2026). Экраны отказа и тексты входа, которые читает ученик.
CARE_TEMPLATES = STUDENT_TEMPLATES + ["blocked.html", "denied.html"]
CARE_SOURCES = ["app/api/auth.py", "app/main.py", "app/services/feature_periods.py"]
# Не ученику: машинный ответ выключенной интеграции n8n (`/auth/internal/issue-link`)
# и вход сотрудников (`/staff/login`).
ADMIN_ALLOWED = {
    "Пользователь отключен администратором.",
    "Аккаунт отключён. Обратитесь к администратору.",
}
ADMIN_WORD = re.compile(r"администратор", re.IGNORECASE)


@pytest.mark.parametrize("name", CARE_TEMPLATES)
def test_student_screen_sends_to_care_not_admin(name):
    source = (TEMPLATES / name).read_text(encoding="utf-8")
    visible = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
    assert not ADMIN_WORD.search(visible), f"{name}: «администратор» – пишите «служба заботы»"


@pytest.mark.parametrize("path", CARE_SOURCES)
def test_student_texts_send_to_care_not_admin(path):
    import ast

    tree = ast.parse((TEMPLATES.parent.parent / path).read_text(encoding="utf-8"))
    found = sorted({
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and ADMIN_WORD.search(node.value) and node.value not in ADMIN_ALLOWED
        and "\n" not in node.value.strip()  # докстринги и комментарии-строки
    })
    assert not found, f"{path}: ученик читает {found} – пишите «служба заботы»"
