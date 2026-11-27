"""Проверка синтаксиса JavaScript отрендеренной страницы дня."""

import pathlib
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta

import pytest

from app.services.tz import today_msk


def test_day_page_scripts_are_valid_javascript(
    client, db, user_factory, session_factory
):
    """Отрендеренный скрипт страницы должен разбираться как JavaScript.

    Сторож для аварии 06.09.2026: в `blockSettingsHTML` цикл Jinja стоял
    внутри строкового литерала JS, и перевод строки перед `{% endfor %}`
    попадал прямо в литерал. Сервер отдавал страницу с кодом 200, тесты были
    зелёными, а браузер падал с `SyntaxError` на всём скрипте — в
    конструкторе не работала ни одна кнопка. Статический сторож выше такого
    не видит: он снимает строковые литералы перед разбором.

    Проверка идёт через `node --check`, потому что ломается именно разбор
    файла. Нет Node — тест пропускается, чтобы не блокировать прогон там,
    где его не поставили.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js не установлен — синтаксис скрипта не проверить")

    admin = user_factory(vk_id=990_103, name="Главный", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    iso = (today_msk() + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{iso}")
    assert page.status_code == 200

    scripts = re.findall(r"<script>(.*?)</script>", page.text, re.S)
    assert scripts, "на странице дня не нашлось ни одного скрипта"

    with tempfile.TemporaryDirectory() as tmp:
        for number, raw in enumerate(scripts):
            path = pathlib.Path(tmp) / f"day_script_{number}.js"
            path.write_text(raw, encoding="utf-8")
            check = subprocess.run(
                [node, "--check", str(path)], capture_output=True, text=True
            )
            assert check.returncode == 0, (
                f"скрипт #{number} страницы дня не разбирается: " + check.stderr
            )
