# -*- coding: utf-8 -*-
"""Браузерный smoke-тест apparchi.ru: обход кабинетов под сессией суперадмина.

Только чтение. Проверяет коды ответов, ошибки консоли, упавшие подзапросы,
битые картинки и горизонтальное переполнение на мобильном; складывает
скриншоты и JSON-отчёт. Секреты берутся из локального .env и не печатаются.

Запуск (нужен playwright с установленным chromium):

    cd portfolio-saas
    python scripts/check_browser.py                       # куда смотрит DNS/hosts
    TARGET_IP=89.23.96.254 OUT_DIR=old python scripts/check_browser.py

TARGET_IP маппит apparchi.ru на конкретный сервер в резолвере Chromium: SNI и
сертификат остаются настоящими, поэтому один и тот же обход можно прогнать
против старого и нового сервера и сравнить отчёты и скриншоты попиксельно.
"""
import json
import os
import pathlib
import sys
from urllib.parse import quote

from playwright.sync_api import sync_playwright

ENV_PATH = pathlib.Path(__file__).resolve().parent.parent / ".env"
OUT = pathlib.Path(os.environ.get("OUT_DIR", "."))
SHOTS = OUT / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)

BASE = "https://apparchi.ru"


def env_value(name: str) -> str:
    for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(name + "="):
            return line[len(name) + 1:].strip().strip("\"'")
    raise SystemExit(f"{name} не найден в .env")


KEY = env_value("ADMIN_ACCESS_TOKEN")

PAGES = [
    ("home", "/"),
    ("login", "/login"),
    ("cabinet", "/cabinet"),
    ("profile", "/cabinet/profile"),
    ("notifications", "/cabinet/notifications"),
    ("portfolio", "/cabinet/portfolio"),
    ("cycle", "/cabinet/cycle"),
    ("curator", "/cabinet/curator"),
    ("curator-reports", "/cabinet/curator/reports"),
    ("curator-portfolio", "/cabinet/curator/portfolio"),
    ("curator-mock-exams", "/cabinet/curator/mock-exams"),
    ("admin-panel", "/cabinet/admin-panel"),
    ("admin-students", "/cabinet/admin/students"),
    ("admin-mock-check", "/cabinet/admin/mock-check"),
    ("staff-students-review", "/cabinet/staff/students-review"),
    ("feedback-list", "/cabinet/feedback/"),
    ("feedback-photo-726", "/cabinet/superadmin/feedback/726"),
    ("feedback-video-720", "/cabinet/superadmin/feedback/720"),
    ("lab3d", "/3dlab"),
]

MEDIA_JS = """() => {
  const imgs = [...document.images];
  return {
    imgs: imgs.length,
    brokenImgs: imgs.filter(i => i.complete && i.naturalWidth === 0)
                    .map(i => i.currentSrc || i.src).slice(0, 10),
    videos: document.querySelectorAll('video, source[type^="video"]').length,
  };
}"""

report = []
console_errs: list[str] = []
failed_reqs: list[str] = []

with sync_playwright() as p:
    # TARGET_IP позволяет прогнать тот же обход против старого прода,
    # минуя hosts: резолвер Chromium маппится точечно, SNI остаётся apparchi.ru.
    target_ip = os.environ.get("TARGET_IP", "").strip()
    launch_args = [f'--host-resolver-rules=MAP apparchi.ru {target_ip}'] if target_ip else []
    if target_ip:
        print(f"цель: {target_ip}", flush=True)
    browser = p.chromium.launch(args=launch_args)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ru-RU")
    page = ctx.new_page()

    def on_console(m):
        if m.type == "error":
            console_errs.append(m.text[:300])

    def on_response(r):
        try:
            if r.status >= 400 and r.request.resource_type != "document":
                failed_reqs.append(f"{r.status} {r.request.resource_type} {r.url[:200]}")
        except Exception:
            pass

    page.on("console", on_console)
    page.on("pageerror", lambda e: console_errs.append("pageerror: " + str(e)[:300]))
    page.on("requestfailed", lambda r: failed_reqs.append(
        f"{(r.failure or '')} {r.url[:200]}"))
    page.on("response", on_response)

    resp = page.goto(f"{BASE}/auth/admin-access?key={quote(KEY)}", wait_until="domcontentloaded")
    has_session = any(c["name"] == "session_id" for c in ctx.cookies())
    print(f"login status: {resp.status} | session cookie: {has_session}", flush=True)
    if not has_session:
        print("СЕССИЯ НЕ ВЫДАНА — дальше смысла нет")
        sys.exit(1)

    for name, url in PAGES:
        console_errs.clear()
        failed_reqs.clear()
        status = final_url = err = None
        try:
            r = page.goto(BASE + url, wait_until="load", timeout=45000)
            status = r.status if r else None
            page.wait_for_timeout(2500)
            final_url = page.url.replace(BASE, "")
        except Exception as e:  # noqa: BLE001
            err = str(e)[:200]
        media = {"imgs": 0, "brokenImgs": [], "videos": 0}
        try:
            media = page.evaluate(MEDIA_JS)
        except Exception:  # noqa: BLE001
            pass
        try:
            page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
        except Exception:  # noqa: BLE001
            pass
        report.append({
            "name": name, "url": url, "status": status, "finalUrl": final_url, "err": err,
            "imgs": media["imgs"], "brokenImgs": media["brokenImgs"], "videos": media["videos"],
            "consoleErrors": list(dict.fromkeys(console_errs))[:8],
            "failedRequests": list(dict.fromkeys(failed_reqs))[:8],
        })
        print(f"{name:<22} {str(status):<4} imgs={media['imgs']} broken={len(media['brokenImgs'])} "
              f"console={len(console_errs)} failed={len(failed_reqs)}", flush=True)

    # мобильный вид ключевых страниц
    mctx = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
        device_scale_factor=2, locale="ru-RU",
        user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
    )
    mctx.add_cookies(ctx.cookies())
    mpage = mctx.new_page()
    mobile = []
    for name, url in [("home", "/"), ("cabinet", "/cabinet"), ("portfolio", "/cabinet/portfolio"),
                      ("staff-students-review", "/cabinet/staff/students-review"),
                      ("feedback-photo-726", "/cabinet/superadmin/feedback/726")]:
        try:
            r = mpage.goto(BASE + url, wait_until="load", timeout=45000)
            mpage.wait_for_timeout(1500)
            ov = mpage.evaluate("() => ({scrollW: document.documentElement.scrollWidth,"
                                " clientW: document.documentElement.clientWidth})")
            mpage.screenshot(path=str(SHOTS / f"m-{name}.png"), full_page=True)
            mobile.append({"name": name, "status": r.status if r else None, **ov,
                           "hOverflow": ov["scrollW"] > ov["clientW"] + 1})
            print(f"m-{name:<20} {r.status if r else None} scrollW={ov['scrollW']} clientW={ov['clientW']}",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            mobile.append({"name": name, "err": str(e)[:150]})

    (OUT / "browser_report.json").write_text(
        json.dumps({"desktop": report, "mobile": mobile}, ensure_ascii=False, indent=2), encoding="utf-8")
    browser.close()

print("готово")
