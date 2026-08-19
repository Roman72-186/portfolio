"""Register the Telegram bot webhook with Telegram.

Run inside the Docker container on the server, once after each deploy where
TELEGRAM_BOT_TOKEN or TELEGRAM_WEBHOOK_SECRET changes:
    docker exec portfolio-saas-app-1 python scripts/set_telegram_webhook.py

Telegram calls back on POST /auth/telegram/webhook — the app validates the
X-Telegram-Bot-Api-Secret-Token header against TELEGRAM_WEBHOOK_SECRET
(see app/dependencies.py::require_telegram_webhook_secret).
"""
import os
import sys

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    domain = os.environ.get("DOMAIN", "apparchi.ru")

    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set", file=sys.stderr)
        sys.exit(1)
    if not secret:
        print("ERROR: TELEGRAM_WEBHOOK_SECRET is not set", file=sys.stderr)
        sys.exit(1)

    webhook_url = f"https://{domain}/auth/telegram/webhook"
    resp = httpx.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": webhook_url,
            "secret_token": secret,
            "allowed_updates": ["message"],
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    print(data)
    if not data.get("ok"):
        sys.exit(1)
    print(f"\nВебхук зарегистрирован: {webhook_url}")


if __name__ == "__main__":
    main()
