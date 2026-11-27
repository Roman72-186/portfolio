"""Карточка оплаты: реальные шаблоны, API и обработчики формы в Chromium."""
from datetime import date

import pytest

from app.services import payments
from test_student_card_manage import people, _login

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
expect = pytest.importorskip('playwright.sync_api').expect


def test_payment_window_changes_only_after_saving(client, db, session_factory, people):
    student = people['student']
    student.pay_window_start, student.pay_window_end = 25, 30
    student.paid_until = payments.paid_until_after(student, date(2026, 11, 1))
    db.commit()
    _login(client, session_factory, people['superadmin'])
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

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
        month.fill('2026-12')
        page.locator('#manage-pay-start').fill('10')
        page.locator('#manage-pay-end').fill('15')
        db.refresh(student)
        assert (student.pay_window_start, student.pay_window_end) == (25, 30)
        assert payments.paid_month(student) == date(2026, 11, 1)
        with page.expect_response(lambda r: r.request.method == 'POST' and '/pay' in r.url):
            page.get_by_role('button', name='Сохранить оплату').click()
        db.refresh(student)
        assert (student.pay_window_start, student.pay_window_end) == (10, 15)
        assert payments.paid_month(student) == date(2026, 12, 1)
        page.reload()
        page.evaluate('(id) => selectStudent(id)', student.id)
        expect(page.locator('#manage-pay-start')).to_have_value('10')
        expect(page.locator('#manage-pay-end')).to_have_value('15')
        expect(month).to_have_value('2026-12')
        browser.close()
