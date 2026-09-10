"""
Тестовая проверка нового бакета Selectel до всякой синхронизации данных (чек-лист 2.1).

Не трогает боевой .env и боевое приложение — все параметры только из переменных
окружения SELECTEL_*. Делает put_object без ACL (так же, как код будет работать
при S3_USE_ACL=false), проверяет публичную доступность объекта по HTTP и удаляет его.

Публичный домен объекта у Selectel — отдельный https://{bucket_uuid}.selstorage.ru,
не совпадает с S3-эндпоинтом для API. bucket_uuid панель показывает при просмотре
ссылки на любой объект бакета (Copy link) — это и есть SELECTEL_S3_PUBLIC_BASE_URL ниже.

Использование:
    export SELECTEL_S3_ENDPOINT=https://s3.ru-3.storage.selcloud.ru
    export SELECTEL_S3_BUCKET=NEW_BUCKET
    export SELECTEL_S3_ACCESS_KEY=...
    export SELECTEL_S3_SECRET_KEY=...
    export SELECTEL_S3_REGION=ru-3
    export SELECTEL_S3_PUBLIC_BASE_URL=https://BUCKET_UUID.selstorage.ru
    python scripts/test_selectel_upload.py
"""
import os
import sys
import uuid

import boto3
import requests
from botocore.config import Config

ENDPOINT = os.environ.get("SELECTEL_S3_ENDPOINT", "")
BUCKET = os.environ.get("SELECTEL_S3_BUCKET", "")
ACCESS_KEY = os.environ.get("SELECTEL_S3_ACCESS_KEY", "")
SECRET_KEY = os.environ.get("SELECTEL_S3_SECRET_KEY", "")
REGION = os.environ.get("SELECTEL_S3_REGION", "ru-1")
PUBLIC_BASE_URL = os.environ.get("SELECTEL_S3_PUBLIC_BASE_URL", "")

if not all([ENDPOINT, BUCKET, ACCESS_KEY, SECRET_KEY]):
    print("Нужны SELECTEL_S3_ENDPOINT, SELECTEL_S3_BUCKET, SELECTEL_S3_ACCESS_KEY, SELECTEL_S3_SECRET_KEY")
    sys.exit(1)

client = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    region_name=REGION,
    config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2, "mode": "standard"}),
)

key = f"test/selectel-check-{uuid.uuid4().hex[:8]}.txt"
body = b"selectel bucket test\n"

print(f"1. put_object (без ACL) -> {key}")
try:
    client.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType="text/plain")
except Exception as exc:
    print(f"   ОШИБКА: {exc}")
    print("   Если жалуется на ACL — этот скрипт уже не передаёт ACL, значит дело в другом (ключи/права).")
    sys.exit(1)
print("   OK")

base = PUBLIC_BASE_URL.rstrip("/") if PUBLIC_BASE_URL else f"{ENDPOINT.rstrip('/')}/{BUCKET}"
public_url = f"{base}/{key}"
print(f"2. Публичный GET без авторизации: {public_url}")
resp = requests.get(public_url, timeout=10)
if resp.status_code == 200 and resp.content == body:
    print("   OK — объект публично читается")
else:
    print(f"   ОШИБКА: код {resp.status_code}, тело {resp.content!r}")
    print("   Проверить bucket policy / тип бакета (public) в панели Selectel.")
    sys.exit(1)

print("3. delete_object")
client.delete_object(Bucket=BUCKET, Key=key)
print("   OK")

print("\nБакет настроен верно: путь path-style, публичное чтение без ACL, ключи рабочие.")
