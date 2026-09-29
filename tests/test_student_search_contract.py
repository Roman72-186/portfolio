"""Render contract for the staff student-list search."""


def test_student_search_normalizes_mobile_input(admin_client):
    client, _ = admin_client

    response = client.get("/cabinet/students")

    assert response.status_code == 200
    # JS экрана с 29.09.2026 — отдельным файлом (шаг 10.3): страница его подключает, сервер отдаёт.
    assert '<script src="/static/js/cabinet_students.js?v=' in response.text
    script = client.get("/static/js/cabinet_students.js")
    assert script.status_code == 200
    html = script.text
    assert "function normalizeStudentSearch(value)" in html
    assert ".normalize('NFKC')" in html
    assert ".trim().replace(/\\s+/g, ' ')" in html
    assert "var queryTokens = q.split(' ')" in html
    assert "queryTokens.every(function(token)" in html


def test_student_list_is_markup_not_script_text(client, session_factory, user_factory):
    """Список учеников должен дойти до браузера разметкой.

    28.09.2026 (`1dc8192`) в `<script>` с CSRF_TOKEN дописали тарифы и потеряли
    закрывающий `</script>`. Сервер отвечал 200, список был в HTML, строковые
    проверки выше оставались зелёными — а браузер читал всю страницу как текст
    скрипта, падал на первом `<` и не показывал ни одного ученика ни ГП, ни
    суперадмину, ни куратору. HTMLParser, как и браузер, не разбирает теги
    внутри `<script>`, поэтому здесь незакрытый скрипт роняет проверку.
    """
    from html.parser import HTMLParser

    user_factory(vk_id=31_001, name="Проверочный ученик", role_name="ученик")
    staff = user_factory(vk_id=31_002, name="ГП", role_name="админ")
    client.cookies.set("session_id", session_factory(staff).id)

    response = client.get("/cabinet/students")
    assert response.status_code == 200

    class _Ids(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids: set[str] = set()

        def handle_starttag(self, tag, attrs):
            element_id = dict(attrs).get("id")
            if element_id:
                self.ids.add(element_id)

    parser = _Ids()
    parser.feed(response.text)
    assert "student-list" in parser.ids
    assert any(i.startswith("srow-") for i in parser.ids)
