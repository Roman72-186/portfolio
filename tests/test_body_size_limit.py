"""Предел тела запроса — 600 МБ (владелец 29.09.2026, код-ревью 28.09 P1 № 3).

Память воркера защищает `read(LIMIT + 1)` в каждом маршруте загрузки, а диск —
нет: Starlette складывает multipart во временный файл до того, как маршрут
сверит размер. Предел стоит в приложении, а не в Traefik: буферизация Traefik
2.11 держит целиком и ответ, и выгрузка суперадмина с перемоткой 3D-файлов
пошла бы через его диск, а ответ больше предела превратился бы в 500.

Самый большой законный запрос — сообщение диалога: фото 25 + видео 500 +
голос 25 МБ. Пачка фото — 20 × 10 МБ.
"""

import pytest
from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient

from app import body_limit
from app.body_limit import BodySizeLimitMiddleware


@pytest.fixture()
def small_limit(monkeypatch):
    monkeypatch.setattr(body_limit, "MAX_REQUEST_BODY_BYTES", 1000)


@pytest.fixture()
def tiny_app():
    calls = []
    app = FastAPI()

    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        calls.append(len(await file.read()))
        return {"ok": True}

    app.add_middleware(BodySizeLimitMiddleware)
    return TestClient(app), calls


def test_limit_is_600_mb():
    assert body_limit.MAX_REQUEST_BODY_BYTES == 600 * 1024 * 1024


def test_declared_oversize_is_refused_before_the_route(small_limit, tiny_app):
    client, calls = tiny_app
    resp = client.post("/upload", files={"file": ("a.jpg", b"x" * 2000)})

    assert resp.status_code == 413
    body = resp.json()
    assert body["error"] and body["error"] == body["detail"]
    assert calls == []


def test_oversize_without_content_length_is_refused(small_limit, tiny_app):
    """Браузер размер заявляет всегда, а скрипт может слать кусками без него."""
    client, calls = tiny_app

    boundary = "limitboundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="a.jpg"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode() + b"x" * 2000 + f"\r\n--{boundary}--\r\n".encode()

    def chunks():
        for start in range(0, len(body), 300):
            yield body[start:start + 300]

    resp = client.post(
        "/upload",
        content=chunks(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert resp.status_code == 413
    assert calls == []


def test_request_under_limit_passes(small_limit, tiny_app):
    client, calls = tiny_app
    resp = client.post("/upload", files={"file": ("a.jpg", b"x" * 100)})

    assert resp.status_code == 200, resp.text
    assert calls == [100]


def test_limit_is_the_outermost_app_middleware():
    """Снаружи всех слоёв приложения: иначе внутренний слой успел бы читать тело."""
    from app.main import app

    assert app.user_middleware[0].cls is BodySizeLimitMiddleware


def test_real_upload_route_refuses_oversize(small_limit, auth_client):
    """То же на боевом приложении: загрузка портфолио отказывает 413 с текстом."""
    client, _ = auth_client
    resp = client.post(
        "/upload/api",
        files={"photos": ("a.jpg", b"x" * 2000, "image/jpeg")},
        headers={"Accept": "application/json"},
    )

    assert resp.status_code == 413
    assert "600 МБ" in resp.json()["detail"]
