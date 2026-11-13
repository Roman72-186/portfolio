"""Render contract for the staff student-list search."""


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


def test_student_card_ignores_slow_response_and_can_retry(client, db, session_factory, user_factory):
    import re

    import pytest

    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    first = user_factory(vk_id=31_010, name="Первый ученик")
    second = user_factory(vk_id=31_011, name="Второй ученик")
    second.tg_username = "a" * 180
    db.commit()
    staff = user_factory(vk_id=31_012, name="ГП", role_name="админ")
    client.cookies.set("session_id", session_factory(staff).id)
    first_url = f"/cabinet/students/{first.id}/profile"
    second_url = f"/cabinet/students/{second.id}/profile"
    held = []
    fail_second = True

    def serve(route):
        if not route.request.url.startswith("https://testserver/"):
            route.abort()
            return
        if route.request.url.endswith(first_url):
            held.append(route)
            return
        if route.request.url.endswith(second_url) and fail_second:
            route.fulfill(status=503, content_type="application/json", body="{}")
            return
        response = client.get(route.request.url, follow_redirects=False)
        route.fulfill(
            status=response.status_code,
            headers={key: value for key, value in response.headers.items() if key != "content-length"},
            body=response.content,
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1024, "height": 768})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", serve)
        page.goto("https://testserver/cabinet/students")
        page.locator(f"#srow-{first.id}").click()
        page.locator("#main-panel").get_by_text("Загружаем…").wait_for()
        page.locator(f"#srow-{second.id}").click()
        panel = page.locator("#main-panel")
        panel.get_by_text("Не удалось открыть карточку ученика.").wait_for()
        fail_second = False
        panel.get_by_role("button", name="Повторить").click()
        panel.get_by_text("Второй", exact=True).wait_for()
        delayed = client.get(first_url)
        with page.expect_response(re.compile(first_url + "$")):
            held[0].fulfill(status=200, content_type="application/json", body=delayed.content)
        page.wait_for_timeout(100)
        assert panel.get_by_text("Второй", exact=True).is_visible()
        assert not errors

        page.set_viewport_size({"width": 320, "height": 680})
        assert panel.locator(".profile-field-value").first.evaluate("(node) => node.scrollWidth <= node.clientWidth")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()
