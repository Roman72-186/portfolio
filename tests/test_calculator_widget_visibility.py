"""Виджет калькулятора курса скрыт от ученика, виден staff (rank >= 4).

app/templates/base.html: {% if user.role_rank >= 4 or settings.course_calculator_all_roles %}
"""


def test_calculator_hidden_for_student(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/tracker")
    assert "course-calc-widget" not in resp.text


def test_calculator_visible_for_admin(admin_client):
    client, _ = admin_client
    resp = client.get("/cabinet/admin-panel")
    assert "course-calc-widget" in resp.text
