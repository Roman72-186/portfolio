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

    # VK OAuth
    vk_app_id: str = ""
    vk_app_secret: str = ""
    vk_redirect_uri: str = "https://apparchi.ru/auth/vk/callback"
    vk_group_id: int = 0
    vk_community_token: str = ""  # service token for re-checking group membership

    # TimeWeb S3
    s3_endpoint: str = ""        # e.g. https://s3.timeweb.cloud
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "ru-1"

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
