"""Render-contract тесты для общего sidebar staff check-экранов.

Закрепляют, что после выноса `partials/staff_check_sidebar.html` экраны
проверки пробников и отработок рендерятся с прежней стабильной разметкой:
back-link, поиск (id/JS-хук), заголовок, контейнер списка.
"""


def test_mock_check_renders_shared_sidebar(admin_client):
    client, _ = admin_client
    resp = client.get("/cabinet/admin/mock-check")
    assert resp.status_code == 200
    html = resp.text
    assert '<a href="/cabinet/admin-panel" class="sidebar-back">← Кабинет</a>' in html
    assert 'Проверка пробников' in html
    assert 'id="student-search"' in html
    assert 'oninput="applyFilters()"' in html
    assert 'placeholder="Поиск ученика…"' in html
    assert '<div class="sidebar-list" id="student-list">' in html


def test_retake_check_renders_shared_sidebar(admin_client):
    client, _ = admin_client
    resp = client.get("/cabinet/admin/retake-check")
    assert resp.status_code == 200
    html = resp.text
    assert '<a href="/cabinet/admin-panel" class="sidebar-back">← Кабинет</a>' in html
    assert 'Проверка отработок' in html
    assert 'id="student-search"' in html
    assert 'oninput="applyFilters()"' in html
    assert 'placeholder="Поиск по имени или @username"' in html
    assert '<div class="sidebar-list" id="student-list">' in html
