"""Restricted CONNECT relay for Apparchi's Telegram traffic.

Run on the Netherlands relay host behind a firewall allowing only Apparchi's IP.
The tunnel carries TLS end to end; this process never sees bot tokens.
"""
import asyncio
import os


ALLOWED_CLIENT = "139.100.237.57"
ALLOWED_TARGETS = {"api.telegram.org:443", "oauth.telegram.org:443"}
PORT = int(os.getenv("APPARCHI_TELEGRAM_PROXY_PORT", "58443"))


async def pipe(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
    try:
        while data := await source.read(65536):
            target.write(data)
            await target.drain()
    finally:
        target.close()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    if not peer or peer[0] != ALLOWED_CLIENT:
        writer.close()
        return
    try:
        line = await asyncio.wait_for(reader.readline(), 5)
        parts = line.decode("ascii", errors="replace").strip().split()
        if len(parts) != 3 or parts[0] != "CONNECT" or parts[1] not in ALLOWED_TARGETS:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        total = len(line)
        while True:
            header = await asyncio.wait_for(reader.readline(), 5)
            total += len(header)
            if total > 8192 or not header:
                return
            if header in (b"\r\n", b"\n"):
                break
        host = parts[1].removesuffix(":443")
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, 443), 10,
        )
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(pipe(reader, upstream_writer), pipe(upstream_reader, writer))
    except (OSError, asyncio.TimeoutError, ValueError):
        pass
    finally:
        writer.close()


async def main() -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
