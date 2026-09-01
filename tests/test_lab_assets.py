"""Роут выдачи контента 3D-лаборатории: доступ только вошедшим, без выхода из каталога.

Смысл роута — закрыть контент, который до 01.09.2026 лежал в открытом чужом
бакете и качался по прямой ссылке кем угодно. Поэтому проверяем не «файл
отдаётся», а границы: без сессии нельзя, наружу каталога нельзя.
"""
import pytest

from app.config import settings


@pytest.fixture()
def assets_dir(tmp_path, monkeypatch):
    """Временный каталог ассетов вместо боевого."""
    root = tmp_path / "lab-assets"
    (root / "models").mkdir(parents=True)
    (root / "models" / "doric.gltf").write_bytes(b'{"asset":{"version":"2.0"}}')
    (root / "models" / "doric.bin").write_bytes(b"\x00\x01\x02")
    # Файл-приманка рядом с каталогом: попытка выйти наружу должна упереться в него.
    (tmp_path / "secret.txt").write_text("не отдавать")
    monkeypatch.setattr(settings, "lab_assets_dir", str(root))
    return root


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def test_anonymous_gets_401(client, assets_dir):
    """Именно 401, а не редирект на вход: fetch и <video> редирект проглотят молча
    и получат HTML со статусом 200 вместо файла."""
    r = client.get("/lab/asset/models/doric.gltf", follow_redirects=False)
    assert r.status_code == 401
    assert "text/html" not in r.headers.get("content-type", "")


def test_logged_in_student_gets_file(client, assets_dir, regular_user, session_factory):
    _login(client, session_factory, regular_user)
    r = client.get("/lab/asset/models/doric.gltf")
    assert r.status_code == 200
    assert r.content == b'{"asset":{"version":"2.0"}}'
    assert r.headers["content-type"].startswith("model/gltf+json")
    # Общим кэшам такой ответ отдавать нельзя: он персонален как сессия.
    assert "private" in r.headers["cache-control"]


def test_bin_served_as_octet_stream(client, assets_dir, regular_user, session_factory):
    _login(client, session_factory, regular_user)
    r = client.get("/lab/asset/models/doric.bin")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/octet-stream")


def test_range_request_supported(client, assets_dir, regular_user, session_factory):
    """Перемотка видео опирается на диапазоны — без них плеер не отматывает."""
    _login(client, session_factory, regular_user)
    r = client.get("/lab/asset/models/doric.bin", headers={"Range": "bytes=1-2"})
    assert r.status_code == 206
    assert r.content == b"\x01\x02"


@pytest.mark.parametrize("path", [
    "../secret.txt",
    "models/../../secret.txt",
    "models/%2e%2e/%2e%2e/secret.txt",
    "/etc/passwd",
    "models\doric.gltf",
])
def test_escape_attempts_rejected(client, assets_dir, regular_user, session_factory, path):
    _login(client, session_factory, regular_user)
    r = client.get(f"/lab/asset/{path}")
    assert r.status_code == 404, f"путь {path!r} не должен отдаваться"
    assert b"\xd0\xbd\xd0\xb5 \xd0\xbe\xd1\x82\xd0\xb4\xd0\xb0\xd0\xb2\xd0\xb0\xd1\x82\xd1\x8c" not in r.content


def test_missing_file_is_404(client, assets_dir, regular_user, session_factory):
    _login(client, session_factory, regular_user)
    assert client.get("/lab/asset/models/nope.gltf").status_code == 404


def test_outsider_gets_403(client, assets_dir, user_factory, session_factory):
    """Вошёл, но к лаборатории отношения не имеет — файла не получает.

    Условие совпадает с проверкой на самой странице /3dlab: ни участия в
    сообществе, ни роли, ни админских прав.
    """
    outsider = user_factory(
        vk_id=100_777, name="Посторонний",
        is_group_member=False, is_admin=False, role_name=None,
    )
    _login(client, session_factory, outsider)
    r = client.get("/lab/asset/models/doric.gltf", follow_redirects=False)
    assert r.status_code == 403
    assert "text/html" not in r.headers.get("content-type", "")
