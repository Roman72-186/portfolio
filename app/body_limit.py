"""Предел тела запроса — защита диска сервера от огромной загрузки.

Код-ревью 28.09.2026 (P1 № 3): память воркера защищает `read(LIMIT + 1)` в
маршрутах загрузки, а диск — нет. Starlette складывает multipart во временный
файл целиком, прежде чем маршрут сверит размер, и тело в гигабайты забило бы
диск. Владелец 29.09.2026 согласился на предел 600 МБ.

Предел стоит здесь, а не в Traefik: буферизация Traefik 2.11 держит у себя
целиком и ответ — выгрузка суперадмина и отдача 3D-файлов с перемоткой пошли
бы через его диск, а ответ больше предела превратился бы в 500.

600 МБ — с запасом над самым большим законным запросом: сообщение диалога
несёт фото 25 + видео 500 + голос 25 МБ, пачка фото — 20 × 10 МБ. Видеоуроки
сюда не относятся: браузер шлёт их в Bunny мимо сервера.

Слой чистый ASGI и стоит снаружи всех слоёв приложения (`app/main.py`).
Заявленный размер больше предела — отказ сразу, тело не читается. Размер не
заявлен (тело кусками) — считаем присланное и обрываем на пределе.
"""

from fastapi import HTTPException
from starlette.responses import JSONResponse

MAX_REQUEST_BODY_BYTES = 600 * 1024 * 1024

TOO_LARGE_MESSAGE = "Файл слишком большой: за один раз принимаем не больше 600 МБ"


class BodySizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = MAX_REQUEST_BODY_BYTES  # читается на каждый запрос — тесты подменяют
        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            response = JSONResponse(
                {"ok": False, "error": TOO_LARGE_MESSAGE, "detail": TOO_LARGE_MESSAGE},
                status_code=413,
            )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI при разборе тела пробрасывает HTTPException как
                    # есть — обработчик в main.py отдаст 413 с этим текстом.
                    raise HTTPException(status_code=413, detail=TOO_LARGE_MESSAGE)
            return message

        await self.app(scope, limited_receive, send)
