"""Проверка синтаксиса маски телефона."""

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PHONE_MASK_JS_PATH = ROOT / "app" / "static" / "js" / "phone-mask.js"


def test_phone_mask_js_is_valid_javascript():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js не установлен — синтаксис скрипта не проверить")

    check = subprocess.run(
        [node, "--check", str(PHONE_MASK_JS_PATH)], capture_output=True, text=True
    )
    assert check.returncode == 0, f"phone-mask.js не разбирается: {check.stderr}"
