"""Карточка оплаты: реальные шаблоны, API и обработчики формы в Chromium."""
from pathlib import Path
from datetime import date

import pytest

from app.services import payments
from test_student_card_manage import people, _login

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
expect = pytest.importorskip('playwright.sync_api').expect


def test_calendar_card_and_unsaved_hint(client, db, session_factory, people):
    student = people['student']
    student.pay_window_start, student.pay_window_end = 25, 30
    student.paid_until = payments.paid_until_after(student, date(2026, 11, 1))
    db.commit()
    _login(client, session_factory, people['superadmin'])
    artifacts = Path('.artifacts/payment-calendar')
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width in (390, 1440):
            page = browser.new_page(viewport={'width': width, 'height': 1000})

            def serve(route):
                req = route.request
                if not req.url.startswith('https://testserver/'):
                    route.abort(); return
                res = client.request(req.method, req.url, content=req.post_data_buffer,
                                     headers={k:v for k,v in req.headers.items() if k not in {'cookie','host'}},
                                     follow_redirects=False)
                route.fulfill(status=res.status_code, body=res.content,
                              headers={k:v for k,v in res.headers.items() if k not in {'content-length','content-encoding','transfer-encoding'}})

            page.route('**/*', serve)
            page.goto('https://testserver/cabinet/students')
            page.evaluate('(id) => selectStudent(id)', student.id)
            month = page.locator('#manage-paid-month')
            expect(month).to_be_visible()
            expect(month).to_have_value('2026-11')
            field = month.locator('..')
            expect(field).to_contain_text('Оплачен месяц обучения')
            expect(field).to_contain_text('декабрь 2026: 25.11.2026 – 30.11.2026')
            month.scroll_into_view_if_needed()
            page.screenshot(path=str(artifacts / f'window-25-30-{width}.png'))
            month.fill('2026-12')
            expect(field).to_contain_text('Сохраните оплату')
            assert '25.11.2026' not in field.inner_text()
            page.locator('#manage-pay-start').fill('10')
            page.locator('#manage-pay-end').fill('15')
            month.fill('2026-11')
            page.get_by_role('button', name='Сохранить оплату', exact=True).click()
            expect(page.locator('#manage-paid-month').locator('..')).to_contain_text('10.12.2026 – 15.12.2026')
            page.screenshot(path=str(artifacts / f'window-10-15-{width}.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.close()
            # Следующий viewport начинает с исходных сохранённых настроек.
            student.pay_window_start, student.pay_window_end = 25, 30
            student.paid_until = payments.paid_until_after(student, date(2026, 11, 1))
            db.commit()
        browser.close()
