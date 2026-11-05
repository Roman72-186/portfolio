"""Receive Telegram updates over an outbound connection when webhook ingress fails."""
import asyncio
import logging

import httpx

from app.config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


async def main():
    api = f"https://api.telegram.org/bot{settings.telegram_bot_token}/"
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10), proxy=settings.telegram_proxy_url or None) as telegram:
        async with httpx.AsyncClient(timeout=45) as local:
            response = await telegram.post(api + "deleteWebhook", json={"drop_pending_updates": False})
            response.raise_for_status()
            if not response.json().get("ok"):
                raise RuntimeError("Telegram refused deleteWebhook")
            log.info("Telegram polling started; pending updates retained")
            offset = None
            while True:
                try:
                    response = await telegram.post(api + "getUpdates", json={
                        "offset": offset, "timeout": 25,
                        "allowed_updates": ["message", "callback_query"],
                    })
                    response.raise_for_status()
                    payload = response.json()
                    if not payload.get("ok"):
                        raise RuntimeError("Telegram refused getUpdates")
                    for update in payload.get("result", []):
                        delivered = await local.post(
                            "http://app:8000/auth/telegram/webhook",
                            json=update,
                            headers={"X-Telegram-Bot-Api-Secret-Token": settings.telegram_webhook_secret},
                        )
                        delivered.raise_for_status()
                        # The next getUpdates acknowledges only successfully handled updates.
                        offset = update["update_id"] + 1
                except (httpx.HTTPError, RuntimeError, KeyError) as exc:
                    log.warning("Telegram polling retry: %s", type(exc).__name__)
                    await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
