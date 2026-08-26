"""Виджет калькулятора курса скрыт на всех ролях и экранах.

app/templates/base.html: {% if settings.course_calculator_all_roles %} — флаг
COURSE_CALCULATOR_ALL_ROLES выключен по умолчанию и нигде не выставлен, так что
виджета нет ни у ученика, ни у staff.
"""


def test_calculator_hidden_for_student(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/student")
    assert "course-calc-widget" not in resp.text


def test_calculator_hidden_for_admin(admin_client):
    client, _ = admin_client
    resp = client.get("/cabinet/admin-panel")
    assert "course-calc-widget" not in resp.text
