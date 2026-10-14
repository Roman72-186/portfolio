"""Служба ключей VPN на сервере Amnezia (план `plans/2026-10-13-vpn-amnezia.md`).

Платформа Apparchi не ходит на VPN-сервер по SSH и не держит root-доступ: она
зовёт эту службу по HTTPS с токеном. Три действия:

    POST   /peers            {"label": "ФИО @ник"} → {"peer_id", "key"}
    DELETE /peers?peer_id=…  → {"deleted": bool}   (повтор безопасен)
    GET    /peers            → {"peers": [{"peer_id", "label", "created_at"}]}
    GET    /health           → {"ok": true}       (без токена)

Пир добавляется в тот же контейнер AmneziaWG, которым владелец управляет из
своего приложения AmneziaVPN, — тем же способом, что и само приложение
(amnezia-client, issue #1615): строка `[Peer]` в конфиге интерфейса, запись в
`clientsTable` (по ней приложение показывает список пользователей) и
`syncconf` без перезапуска интерфейса.

Ключ `vpn://` собирается по **эталону** — гостевому ключу, который владелец один
раз выпустил в приложении («Поделиться» → «Поделиться VPN»). Из эталона берутся
адрес и порт сервера, параметры обфускации и раскладка JSON ровно той версии
Amnezia, что стоит на сервере; меняются только поля клиента: ключи, адрес и
текст конфига. Так формат не надо угадывать по памяти, а после обновления
Amnezia достаточно положить новый эталон.

Пиры, созданные службой, она помнит в своём файле состояния: GET /peers
отдаёт только их, и сверка на стороне платформы никогда не тронет ключи,
которые владелец выпустил сам.

Только стандартная библиотека: на сервере нужен лишь python3 (в Ubuntu он есть).
Настройки — переменные окружения `VPN_KEYS_*` (см. `Config.from_env`).
"""
from __future__ import annotations

import base64
import contextlib
import hmac
import ipaddress
import json
import logging
import os
import re
import ssl
import struct
import subprocess
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("vpn_keys")

MAX_LABEL = 120
MAX_BODY = 4096


# ── Формат vpn:// ───────────────────────────────────────────────────────────
# JSON → qCompress (4 байта длины big-endian + поток zlib) → base64url без `=`.
# Так его пишет и читает AmneziaVPN (Qt `qCompress`/`qUncompress`).

def decode_vpn_key(key: str) -> dict:
    raw = key.strip()
    if raw.startswith("vpn://"):
        raw = raw[len("vpn://"):]
    raw += "=" * (-len(raw) % 4)
    packed = base64.urlsafe_b64decode(raw)
    return json.loads(zlib.decompress(packed[4:]).decode("utf-8"))


def encode_vpn_key(data: dict) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    packed = struct.pack(">I", len(raw)) + zlib.compress(raw)
    return "vpn://" + base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")


def _awg_container(template: dict) -> dict:
    """Блок контейнера AmneziaWG в эталоне. В гостевом ключе он один."""
    for container in template.get("containers") or []:
        if isinstance(container.get("awg"), dict):
            return container
    raise ValueError("в эталоне нет контейнера AmneziaWG (поле containers[].awg)")


def template_container_name(template: dict) -> str:
    """Имя docker-контейнера на сервере (`amnezia-awg2` у AmneziaWG 3.1)."""
    return _awg_container(template)["container"]


def _replace_conf_line(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^(\s*{key}\s*=\s*).*$", re.MULTILINE)
    if not pattern.search(text):
        raise ValueError(f"в конфиге эталона нет строки {key}")
    return pattern.sub(lambda m: m.group(1) + value, text, count=1)


def build_client_key(template: dict, *, private_key: str, public_key: str,
                     psk: str, address: str) -> str:
    """Ключ нового клиента: эталон, в котором заменены только поля клиента.

    `last_config` в ключах Amnezia — JSON, упакованный строкой внутри JSON;
    в нём и ключи клиента, и полный текст конфига (`config`). Меняем и то и
    другое, остальное (сервер, порт, Jc/S/H/I) остаётся как в эталоне."""
    data = json.loads(json.dumps(template))  # глубокая копия
    awg = _awg_container(data)["awg"]
    last = awg["last_config"]
    last_is_str = isinstance(last, str)
    cfg = json.loads(last) if last_is_str else dict(last)

    cfg["client_priv_key"] = private_key
    cfg["client_pub_key"] = public_key
    cfg["psk_key"] = psk
    cfg["client_ip"] = address
    text = cfg.get("config")
    if not isinstance(text, str):
        raise ValueError("в эталоне нет текста конфига (last_config.config)")
    text = _replace_conf_line(text, "PrivateKey", private_key)
    text = _replace_conf_line(text, "Address", f"{address}/32")
    text = _replace_conf_line(text, "PresharedKey", psk)
    cfg["config"] = text

    awg["last_config"] = json.dumps(cfg, ensure_ascii=False) if last_is_str else cfg
    return encode_vpn_key(data)


# ── Конфиг интерфейса на сервере ────────────────────────────────────────────

def _interface_network(conf: str) -> tuple[ipaddress.IPv4Network, ipaddress.IPv4Address]:
    m = re.search(r"^\s*\[Interface\][^\[]*?^\s*Address\s*=\s*([^\s,]+)", conf, re.MULTILINE | re.DOTALL)
    if not m:
        raise ValueError("в конфиге сервера нет [Interface] Address")
    iface = ipaddress.ip_interface(m.group(1))
    return iface.network, iface.ip


def used_addresses(conf: str) -> set[ipaddress.IPv4Address]:
    used = set()
    for m in re.finditer(r"^\s*AllowedIPs\s*=\s*(.+)$", conf, re.MULTILINE):
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            net = ipaddress.ip_network(part, strict=False)
            if net.version == 4 and net.prefixlen == 32:
                used.add(net.network_address)
    return used


def next_free_address(conf: str) -> str:
    network, server_ip = _interface_network(conf)
    taken = used_addresses(conf) | {server_ip}
    for host in network.hosts():
        if host not in taken:
            return str(host)
    raise RuntimeError(f"в подсети {network} кончились адреса")


def peer_block(public_key: str, psk: str, address: str) -> str:
    return (
        "\n[Peer]\n"
        f"PublicKey = {public_key}\n"
        f"PresharedKey = {psk}\n"
        f"AllowedIPs = {address}/32\n"
    )


def remove_peer(conf: str, public_key: str) -> tuple[str, bool]:
    """Конфиг без секции `[Peer]` с этим ключом. Второе значение — нашлась ли."""
    parts = re.split(r"(?=^\s*\[)", conf, flags=re.MULTILINE)
    kept, found = [], False
    key_line = re.compile(rf"^\s*PublicKey\s*=\s*{re.escape(public_key)}\s*$", re.MULTILINE)
    for part in parts:
        if re.match(r"\s*\[Peer\]", part) and key_line.search(part):
            found = True
            continue
        kept.append(part)
    text = "".join(kept)
    return (text if text.endswith("\n") or not text else text + "\n"), found


def peer_public_keys(conf: str) -> set[str]:
    keys = set()
    for part in re.split(r"(?=^\s*\[)", conf, flags=re.MULTILINE):
        if re.match(r"\s*\[Peer\]", part):
            keys.update(re.findall(r"^\s*PublicKey\s*=\s*(\S+)\s*$", part, re.MULTILINE))
    return keys


# ── Работа с контейнером ────────────────────────────────────────────────────

@dataclass
class Config:
    token: str
    template_path: str
    state_path: str
    lock_path: str
    conf_path: str
    clients_path: str
    tool: str
    interface: str
    container: str | None
    bind: str
    port: int
    cert: str | None
    key: str | None

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        conf_path = env.get("VPN_KEYS_CONF", "/opt/amnezia/awg/awg0.conf")  # AmneziaWG 3.1
        return cls(
            token=env["VPN_KEYS_TOKEN"],
            template_path=env.get("VPN_KEYS_TEMPLATE", "/etc/vpn-keys/template.vpn"),
            state_path=env.get("VPN_KEYS_STATE", "/var/lib/vpn-keys/peers.json"),
            lock_path=env.get("VPN_KEYS_LOCK", "/run/vpn-keys.lock"),
            conf_path=conf_path,
            clients_path=env.get("VPN_KEYS_CLIENTS", "/opt/amnezia/awg/clientsTable"),
            tool=env.get("VPN_KEYS_TOOL", "awg"),
            interface=env.get("VPN_KEYS_IFACE", os.path.splitext(os.path.basename(conf_path))[0]),
            container=env.get("VPN_KEYS_CONTAINER") or None,
            bind=env.get("VPN_KEYS_BIND", "0.0.0.0"),
            port=int(env.get("VPN_KEYS_PORT", "8443")),
            cert=env.get("VPN_KEYS_CERT") or None,
            key=env.get("VPN_KEYS_KEY") or None,
        )


class Docker:
    """Команды внутри контейнера AmneziaWG. Отдельным классом, чтобы тесты
    подменяли его без docker."""

    def __init__(self, container: str, tool: str):
        self.container = container
        self.tool = tool

    def _run(self, args: list[str], stdin: str | None = None) -> str:
        res = subprocess.run(
            ["docker", "exec", "-i", self.container, *args],
            input=stdin, capture_output=True, text=True, timeout=20,
        )
        if res.returncode != 0:
            raise RuntimeError(f"{' '.join(args[:2])}: {res.stderr.strip()[:300]}")
        return res.stdout

    def read(self, path: str) -> str:
        return self._run(["cat", path])

    def write(self, path: str, content: str) -> None:
        # Через временный файл и mv: оборванная запись не оставит полконфига.
        self._run(["sh", "-c", f'cat > "{path}.tmp" && mv "{path}.tmp" "{path}"'], stdin=content)

    def genkey(self) -> tuple[str, str]:
        private = self._run([self.tool, "genkey"]).strip()
        public = self._run([self.tool, "pubkey"], stdin=private + "\n").strip()
        return private, public

    def genpsk(self) -> str:
        return self._run([self.tool, "genpsk"]).strip()

    def syncconf(self, interface: str, conf_path: str) -> None:
        stripped = "/tmp/vpn-keys-strip.conf"
        self._run(["sh", "-c",
                   f'{self.tool}-quick strip "{conf_path}" > {stripped} '
                   f'&& {self.tool} syncconf {interface} {stripped} && rm -f {stripped}'])


class PeerStore:
    def __init__(self, cfg: Config, docker: Docker, template: dict):
        self.cfg = cfg
        self.docker = docker
        self.template = template

    @contextlib.contextmanager
    def _lock(self):
        import fcntl  # только Linux; тесты на Windows подменяют _lock
        os.makedirs(os.path.dirname(self.cfg.lock_path) or ".", exist_ok=True)
        with open(self.cfg.lock_path, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _load_state(self) -> dict:
        try:
            with open(self.cfg.state_path, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {}

    def _save_state(self, state: dict) -> None:
        os.makedirs(os.path.dirname(self.cfg.state_path) or ".", exist_ok=True)
        tmp = self.cfg.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.cfg.state_path)

    def _load_clients(self) -> list:
        try:
            raw = self.docker.read(self.cfg.clients_path)
        except RuntimeError:
            return []
        try:
            data = json.loads(raw or "[]")
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def create(self, label: str) -> dict:
        with self._lock():
            conf = self.docker.read(self.cfg.conf_path)
            address = next_free_address(conf)
            private, public = self.docker.genkey()
            psk = self.docker.genpsk()
            key = build_client_key(self.template, private_key=private, public_key=public,
                                   psk=psk, address=address)
            now = datetime.now(timezone.utc)

            self.docker.write(self.cfg.conf_path, conf.rstrip("\n") + "\n" + peer_block(public, psk, address))
            clients = self._load_clients()
            clients.append({
                "clientId": public,
                "userData": {
                    # Поля и формат — как пишет само приложение AmneziaVPN
                    # (сверено с сервером 14.10.2026, AmneziaWG 3.1).
                    "allowed_ips": f"{address}/32",
                    "clientName": label,
                    "creationDate": now.astimezone().strftime("%a %b %d %H:%M:%S %Y"),
                },
            })
            self.docker.write(self.cfg.clients_path, json.dumps(clients, ensure_ascii=False, indent=4))
            self.docker.syncconf(self.cfg.interface, self.cfg.conf_path)

            state = self._load_state()
            state[public] = {"label": label, "address": address, "created_at": now.isoformat()}
            self._save_state(state)
        logger.info("peer created address=%s label=%r", address, label)
        return {"peer_id": public, "key": key}

    def delete(self, peer_id: str) -> bool:
        with self._lock():
            state = self._load_state()
            conf = self.docker.read(self.cfg.conf_path)
            new_conf, found = remove_peer(conf, peer_id)
            if found:
                self.docker.write(self.cfg.conf_path, new_conf)
                clients = [c for c in self._load_clients() if c.get("clientId") != peer_id]
                self.docker.write(self.cfg.clients_path, json.dumps(clients, ensure_ascii=False, indent=4))
                self.docker.syncconf(self.cfg.interface, self.cfg.conf_path)
            known = state.pop(peer_id, None) is not None
            if known:
                self._save_state(state)
        logger.info("peer delete peer=%s…found=%s", peer_id[:8], found)
        return found or known

    def list(self) -> list[dict]:
        """Только свои пиры, и только те, что ещё стоят в конфиге: если
        владелец удалил ученика в приложении, сверка увидит пропажу."""
        with self._lock():
            state = self._load_state()
            present = peer_public_keys(self.docker.read(self.cfg.conf_path))
        return [
            {"peer_id": pid, "label": info.get("label", ""), "created_at": info.get("created_at")}
            for pid, info in state.items() if pid in present
        ]


# ── HTTP ────────────────────────────────────────────────────────────────────

def make_handler(store: PeerStore, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "vpn-keys"

        def log_message(self, fmt, *args):  # в журнал systemd, без токенов
            logger.info("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            given = header[len("Bearer "):] if header.startswith("Bearer ") else ""
            return bool(given) and hmac.compare_digest(given.encode(), token.encode())

        def _guard(self) -> bool:
            if not self._authorized():
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return False
            return True

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                return self._send(HTTPStatus.OK, {"ok": True})
            if not self._guard():
                return
            if path == "/peers":
                try:
                    return self._send(HTTPStatus.OK, {"peers": store.list()})
                except Exception as exc:
                    logger.exception("list failed")
                    return self._send(HTTPStatus.BAD_GATEWAY, {"error": str(exc)[:200]})
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            if not self._guard():
                return
            if urlparse(self.path).path != "/peers":
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "bad body"})
            try:
                body = json.loads(self.rfile.read(length))
                label = str(body.get("label") or "").strip()[:MAX_LABEL]
            except (ValueError, AttributeError):
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "bad json"})
            if not label:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "label required"})
            try:
                return self._send(HTTPStatus.CREATED, store.create(label))
            except Exception as exc:
                logger.exception("create failed")
                return self._send(HTTPStatus.BAD_GATEWAY, {"error": str(exc)[:200]})

        def do_DELETE(self):
            if not self._guard():
                return
            parsed = urlparse(self.path)
            if parsed.path != "/peers":
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            peer_id = (parse_qs(parsed.query).get("peer_id") or [""])[0].strip()
            if not peer_id:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "peer_id required"})
            try:
                return self._send(HTTPStatus.OK, {"deleted": store.delete(peer_id)})
            except Exception as exc:
                logger.exception("delete failed")
                return self._send(HTTPStatus.BAD_GATEWAY, {"error": str(exc)[:200]})

    return Handler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = Config.from_env()
    with open(cfg.template_path, encoding="utf-8") as fh:
        template = decode_vpn_key(fh.read())
    container = cfg.container or template_container_name(template)
    docker = Docker(container, cfg.tool)
    # Проверка на старте: контейнер жив, конфиг читается, адреса считаются,
    # эталон собирается. Иначе ошибка всплыла бы только на первом ученике.
    conf = docker.read(cfg.conf_path)
    next_free_address(conf)
    build_client_key(template, private_key="x", public_key="y", psk="z", address="10.0.0.2")
    store = PeerStore(cfg, docker, template)

    httpd = ThreadingHTTPServer((cfg.bind, cfg.port), make_handler(store, cfg.token))
    if cfg.cert and cfg.key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cfg.cert, cfg.key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    logger.info("vpn-keys: container=%s conf=%s listen=%s:%s tls=%s",
                container, cfg.conf_path, cfg.bind, cfg.port, bool(cfg.cert))
    httpd.serve_forever()


if __name__ == "__main__":
    main()
