"""The relay must refuse CONNECT targets outside Telegram."""
import asyncio

from scripts import telegram_connect_proxy as proxy


def test_connect_proxy_rejects_other_targets(monkeypatch):
    monkeypatch.setattr(proxy, "ALLOWED_CLIENT", "127.0.0.1")

    async def exercise():
        server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
            await writer.drain()
            assert await reader.readline() == b"HTTP/1.1 403 Forbidden\r\n"
            writer.close()
            await writer.wait_closed()

    asyncio.run(exercise())
