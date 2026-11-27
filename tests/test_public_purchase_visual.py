"""Проверка настоящих шаблонов в Chromium без запросов к внешним сервисам.

Запуск: python -m pytest tests/test_public_purchase_visual.py -q
Требует уже установленного Playwright/Chromium; продукт от него не зависит.
"""

import pytest

sync_playwright = pytest.importorskip('playwright.sync_api', reason='Для визуального прогона нужен Playwright').sync_playwright

from test_public_purchase import sales, buyer, _payment, _settle, _profile, INVITE


def test_buyer_can_share_payment_and_open_invitation_after_profile(client, db, buyer):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(service_workers='block')

        def serve(route):
            request = route.request
            if not request.url.startswith('https://testserver/'):
                route.abort()
                return
            response = client.request(request.method, request.url,
                                      content=request.post_data_buffer,
                                      headers={k: v for k, v in request.headers.items()
                                               if k not in {'host', 'cookie'}},
                                      follow_redirects=False)
            headers = {k: v for k, v in response.headers.items()
                       if k not in {'content-length', 'content-encoding', 'transfer-encoding'}}
            route.fulfill(status=response.status_code, headers=headers, body=response.content)

        page.route('**/*', serve)
        for route in ['/purchase/return?paid=1', '/purchase']:
            page.goto('https://testserver' + route)
            page.locator('h1').wait_for()
            assert not page.locator(f'a[href="{INVITE}"]').count()
        # Реальный запрос к API, разрешённое копирование и отказ браузера.
        page.evaluate("() => { window.copyText = async text => { window.copiedPayment = text; }; }")
        page.get_by_role('button', name='Скопировать ссылку для родителя').click()
        page.wait_for_function("Boolean(window.copiedPayment)")
        copied = page.evaluate('window.copiedPayment')
        assert copied.startswith('https://purchase-test.payform.ru/')
        assert INVITE not in copied
        page.evaluate("() => { window.copyText = async () => { throw new Error('denied'); }; }")
        page.get_by_role('button', name='Скопировать ссылку для родителя').click()
        page.locator('#parent-payment-url').wait_for(state='visible')
        assert page.locator('#parent-payment-url').input_value() == copied
        page.route('**/purchase/payment-link', lambda route: route.fulfill(
            status=409, content_type='application/json', body='{"error":"Payment unavailable"}'))
        page.get_by_role('button', name='Скопировать ссылку для родителя').click()
        page.wait_for_function("document.getElementById('parent-payment-status').textContent === 'Payment unavailable'")
        assert not page.locator('#parent-payment-url').is_visible()
        assert page.locator('#parent-payment-copy').is_enabled()
        page.close()

        _settle(db, _payment(db, buyer))
        page = browser.new_page()
        page.route('**/*', serve)
        page.goto('https://testserver/purchase')
        page.get_by_role('link', name='Заполнить анкету', exact=True).click()
        assert page.url.endswith('/cabinet/profile')
        assert not page.locator('#tariff-select').count()
        client.post('/cabinet/profile', data=_profile(), follow_redirects=False)
        page.goto('https://testserver/purchase')
        assert page.get_by_role('link', name='Перейти в канал', exact=True).get_attribute('href') == INVITE
        assert page.get_by_role('button', name='Проверить участие').is_visible()
        browser.close()
