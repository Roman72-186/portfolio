from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Runtime environment ("production" hides /docs, /redoc, /openapi.json)
    env: str = "production"

    # Database
    database_url: str = "postgresql://portfolio:secret@db:5432/portfolio"

    # Auth / Sessions
    session_secret: str = "change-me"
    pii_encryption_secret: str = ""  # separate Fernet key/secret; falls back to session_secret for legacy rows
    session_ttl_hours: int = 24
    one_time_link_ttl_minutes: int = 30
    internal_api_token: str = ""

    # Domain
    domain: str = "apparchi.ru"

    # Google Drive
    google_drive_parent_id: str = "1fb5GyudhpI013B4EQsZ6nzOoxVRtfn1g"
    google_credentials_path: str = "/app/credentials.json"

    # n8n
    n8n_enabled: bool = False  # disabled: S3 remains the only photo storage
    n8n_base_url: str = "https://n8n-new.twc1.net"
    n8n_webhook_upload: str = "https://n8n-new.twc1.net/webhook/TMEog8ATVv9CW6xh/webhook/portfolio-upload"
    n8n_webhook_download_file: str = ""  # portfolio-download-file webhook URL (for Drive→S3 migration)
    n8n_webhook_secret: str = ""  # sent as X-Webhook-Secret when configured

    # VK OAuth (переходный период — заменяется Telegram-ботом, см. ниже; отключается
    # очисткой vk_app_id/vk_app_secret/vk_group_id, читает _vk_login_enabled() в auth.py)
    vk_app_id: str = ""
    vk_app_secret: str = ""
    vk_redirect_uri: str = "https://apparchi.ru/auth/vk/callback"
    vk_group_id: int = 0
    vk_community_token: str = ""  # service token for re-checking group membership

    # Telegram bot — прямая интеграция (без n8n): вход через /start в боте с
    # проверкой членства в закрытом канале, плюс канал уведомлений.
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""   # для deep-link t.me/<username>?start=...
    telegram_channel_id: int = 0      # закрытый канал, членство проверяет getChatMember
    telegram_webhook_secret: str = "" # сверяется с X-Telegram-Bot-Api-Secret-Token
    telegram_link_ttl_hours: int = 72 # TTL ссылки-приглашения для действующих учеников

    # Telegram Login (OIDC) — основной способ входа на сайте, заменяет
    # deep-link на бота (тот остаётся для /start-привязки существующих
    # учеников и как канал уведомлений). Client ID/Secret выдаёт @BotFather
    # (раздел Login Widget бота), вписывает в .env.prod владелец.
    telegram_login_client_id: str = ""
    telegram_login_client_secret: str = ""
    telegram_login_redirect_uri: str = "https://apparchi.ru/auth/telegram-login/callback"

    # Web Push (Фаза 3) — VAPID-ключи выпускает и вписывает в .env.prod владелец
    vapid_public_key: str = ""   # отдаётся браузеру для подписки (pushManager.subscribe)
    vapid_private_key: str = ""  # серверный ключ подписи push-сообщений, не покидает сервер
    vapid_claim_email: str = ""  # mailto: в VAPID claims, требование push-сервисов (FCM и др.)

    # S3 (TimeWeb сейчас, Selectel — после миграции)
    s3_endpoint: str = ""        # e.g. https://s3.timeweb.cloud — API-эндпоинт для boto3 (put/get/delete)
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "ru-1"
    # Публичный домен для отдачи объектов, если отличается от S3_ENDPOINT+S3_BUCKET.
    # У Selectel это отдельный домен вида https://{bucket_uuid}.selstorage.ru — тот же
    # домен, что и S3_ENDPOINT, объекты анонимно не отдаёт (проверено на боевом бакете).
    # Пусто — используется старое поведение TimeWeb: {S3_ENDPOINT}/{S3_BUCKET}/{путь}.
    s3_public_base_url: str = ""
    # TimeWeb делает объект публичным через Object ACL, Selectel его не поддерживает
    # (там публичность задаётся типом бакета / bucket policy). Default=True сохраняет
    # текущее поведение; на Selectel выставить S3_USE_ACL=false.
    s3_use_acl: bool = True

    # Superadmin permanent access link
    admin_access_token: str = ""
    admin_staff_login: str = "roman.m"  # staff_login of the superadmin account

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # 3D Lab SSO
    sso_token_ttl_minutes: int = 2   # short-lived cross-service token TTL
    lab3d_url: str = ""              # e.g. https://3dlab.example.com
    lab3d_internal_token: str = ""   # shared secret for /auth/internal/sso/verify

    # Course calculator
    course_calculator_all_roles: bool = False  # set true to show calculator on login and widget to all logged-in users

    # Bunny Stream pilot video
    bunny_stream_enabled: bool = False
    bunny_stream_library_id: int = 0
    bunny_stream_video_id: str = ""
    bunny_stream_token_key: str = ""
    bunny_stream_api_key: str = ""
    bunny_stream_token_ttl_seconds: int = 300
    bunny_stream_video_title: str = "Тестовый видеоурок"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    @field_validator("telegram_bot_username")
    @classmethod
    def _strip_bot_username_at(cls, v: str) -> str:
        """t.me/username-ссылки не понимают ведущий @ — люди привычно вводят
        юзернейм бота с ним (как везде в интерфейсе Telegram), нормализуем
        один раз здесь, а не в каждом месте, где строится deep-link."""
        return v.lstrip("@")


settings = Settings()
