"""Ученик видит только «Преподавателя» (владелец 05.10.2026: «ученик НИКОГДА
НЕ ДОЛЖЕН ЗНАТЬ, ЧТО ЕМУ ПИШЕТ КТО-ТО КРОМЕ ПРЕПОДАВАТЕЛЯ»). Инвариант —
`docs/invariants/feedback.md`.

Подпись в диалогах ОС держит `services/feedback.py::dialog_sender` и его
тесты; здесь — сквозная проверка, что имя сотрудника до ученика не доходит.
Сторожа по исходникам шаблонов сняты 20.10.2026
(`plans/2026-10-20-тесты-чистка.md`)."""


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
    assert "Разбор от первого" in resp.text and "Разбор от второго" in resp.text
    assert "Кураторов" not in resp.text and "Анна Главная" not in resp.text
