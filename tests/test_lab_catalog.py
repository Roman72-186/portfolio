"""The server catalog must stay aligned with the browser's card tree."""
from pathlib import Path
import shutil
import subprocess

import pytest

from app.services.lab_access import catalog


def test_catalog_matches_browser_modules():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to check the JavaScript card catalog")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, "scripts/build_lab_catalog.js", "--check"], cwd=root, check=True)
    assert len(catalog()) == 68
    assert all(card["id"] and card["title"] and card["path"] for card in catalog().values())
