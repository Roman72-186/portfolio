"""Проверка синтаксиса скрипта подсказок."""

import pathlib
import shutil
import subprocess

import pytest

HINT_JS_PATH = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "hint.js"


def test_hint_js_is_valid_javascript():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js не установлен — синтаксис скрипта не проверить")

    check = subprocess.run(
        [node, "--check", str(HINT_JS_PATH)], capture_output=True, text=True
    )
    assert check.returncode == 0, f"hint.js не разбирается: {check.stderr}"
