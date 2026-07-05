"""Render contract for the staff student-list search."""


def test_student_search_normalizes_mobile_input(admin_client):
    client, _ = admin_client

    response = client.get("/cabinet/students")

    assert response.status_code == 200
    html = response.text
    assert "function normalizeStudentSearch(value)" in html
    assert ".normalize('NFKC')" in html
    assert ".trim().replace(/\\s+/g, ' ')" in html
    assert "var queryTokens = q.split(' ')" in html
    assert "queryTokens.every(function(token)" in html
