"""Проверка настоящих шаблонов в Chromium без запросов к внешним сервисам.

Запуск: python -m pytest tests/test_public_purchase_visual.py -q
Требует уже установленного Playwright/Chromium; продукт от него не зависит.
"""
from pathlib import Path

import pytest

sync_playwright = pytest.importorskip('playwright.sync_api', reason='Для визуального прогона нужен Playwright').sync_playwright

from test_public_purchase import sales, buyer, _payment, _settle, _profile, INVITE


def test_purchase_mobile_and_desktop(client, db, buyer):
    artifacts = Path('.artifacts/public-purchase')
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width, height in [(320, 740), (390, 844), (1440, 1000)]:
            page = browser.new_page(viewport={'width': width, 'height': height})

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
            for route, label in [('/tariffs', 'tariffs'), ('/purchase', 'unpaid')]:
                page.goto('https://testserver' + route)
                page.locator('h1').wait_for()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not page.locator(f'a[href="{INVITE}"]').count()
                page.screenshot(path=str(artifacts / f'{label}-{width}.png'), full_page=True)
            page.close()

        _settle(db, _payment(db, buyer))
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.route('**/*', serve)
        page.goto('https://testserver/purchase')
        page.screenshot(path=str(artifacts / 'paid-before-profile-390.png'), full_page=True)
        page.get_by_role('link', name='Заполнить анкету', exact=True).click()
        assert page.url.endswith('/cabinet/profile')
        assert not page.locator('#tariff-select').count()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(artifacts / 'annual-profile-390.png'), full_page=True)
        client.post('/cabinet/profile', data=_profile(), follow_redirects=False)
        page.goto('https://testserver/purchase')
        assert page.get_by_role('link', name='Перейти в канал', exact=True).get_attribute('href') == INVITE
        assert page.get_by_role('button', name='Проверить участие').is_visible()
        assert (page.get_by_role('link', name='Перейти в канал', exact=True).bounding_box()['y']
                < page.get_by_role('button', name='Проверить участие').bounding_box()['y'])
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(artifacts / 'invitation-390.png'), full_page=True)
        page.evaluate("localStorage.setItem('apparchi-theme', 'dark')")
        page.reload()
        page.screenshot(path=str(artifacts / 'invitation-dark-390.png'), full_page=True)
        browser.close()
