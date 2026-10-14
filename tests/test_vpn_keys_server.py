"""Служба ключей VPN (`deploy/vpn_keys/vpn_keys_server.py`).

Контейнер AmneziaWG подменён словарём файлов: проверяем формат `vpn://`,
выбор адреса, правку конфига и `clientsTable`, HTTP-контракт и токен.
Эталон собран по раскладке гостевого ключа Amnezia (`containers[].awg.last_config`
— JSON строкой внутри JSON); на живом сервере его заменит настоящий эталон
владельца, тест на нём проверяется так же.
"""
import contextlib
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy" / "vpn_keys"))
import vpn_keys_server as vks  # noqa: E402

TOKEN = "test-vpn-token"
CONF_PATH = "/opt/amnezia/awg/awg0.conf"
CLIENTS_PATH = "/opt/amnezia/awg/clientsTable"

SERVER_CONF = """[Interface]
PrivateKey = SERVERPRIV=
Address = 10.8.1.0/24
ListenPort = 5858
Jc = 4
Jmin = 10
Jmax = 50

[Peer]
PublicKey = OWNERPUB=
PresharedKey = OWNERPSK=
AllowedIPs = 10.8.1.1/32
"""

CLIENT_CONF = """[Interface]
Address = 10.8.1.1/32
DNS = 1.1.1.1, 1.0.0.1
PrivateKey = OWNERPRIV=
Jc = 4
Jmin = 10
Jmax = 50

[Peer]
PublicKey = SERVERPUB=
PresharedKey = OWNERPSK=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 203.0.113.10:5858
PersistentKeepalive = 25
"""


def _template() -> dict:
    last_config = {
        "H1": "1", "Jc": "4", "Jmax": "50", "Jmin": "10",
        "client_ip": "10.8.1.1", "client_priv_key": "OWNERPRIV=",
        "client_pub_key": "OWNERPUB=", "psk_key": "OWNERPSK=",
        "server_pub_key": "SERVERPUB=", "hostName": "203.0.113.10",
        "port": 5858, "mtu": "1376", "config": CLIENT_CONF,
    }
    return {
        "containers": [{
            "container": "amnezia-awg2",
            "awg": {"port": "5858", "transport_proto": "udp",
                    "last_config": json.dumps(last_config)},
        }],
        "defaultContainer": "amnezia-awg2",
        "description": "Apparchi VPN",
        "dns1": "1.1.1.1", "dns2": "1.0.0.1",
        "hostName": "203.0.113.10",
    }


class FakeDocker:
    def __init__(self):
        self.files = {CONF_PATH: SERVER_CONF, CLIENTS_PATH: json.dumps([
            {"clientId": "OWNERPUB=", "userData": {"clientName": "Владелец"}},
        ])}
        self.synced = 0
        self._n = 0

    def read(self, path):
        if path not in self.files:
            raise RuntimeError("no such file")
        return self.files[path]

    def write(self, path, content):
        self.files[path] = content

    def genkey(self):
        self._n += 1
        return f"PRIV{self._n}=", f"PUB{self._n}="

    def genpsk(self):
        return f"PSK{self._n}="

    def syncconf(self, interface, conf_path):
        self.synced += 1


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_KEYS_TOKEN", TOKEN)
    monkeypatch.setenv("VPN_KEYS_STATE", str(tmp_path / "peers.json"))
    cfg = vks.Config.from_env()
    s = vks.PeerStore(cfg, FakeDocker(), _template())
    monkeypatch.setattr(s, "_lock", contextlib.nullcontext)
    return s


# ── формат ──────────────────────────────────────────────────────────────────

def test_encode_decode_roundtrip():
    data = _template()
    key = vks.encode_vpn_key(data)
    assert key.startswith("vpn://") and "=" not in key
    assert vks.decode_vpn_key(key) == data


def test_build_client_key_swaps_only_client_fields():
    key = vks.build_client_key(_template(), private_key="NEWPRIV=", public_key="NEWPUB=",
                               psk="NEWPSK=", address="10.8.1.7")
    data = vks.decode_vpn_key(key)
    cfg = json.loads(data["containers"][0]["awg"]["last_config"])
    assert cfg["client_priv_key"] == "NEWPRIV=" and cfg["client_ip"] == "10.8.1.7"
    assert "PrivateKey = NEWPRIV=" in cfg["config"]
    assert "Address = 10.8.1.7/32" in cfg["config"]
    assert "PresharedKey = NEWPSK=" in cfg["config"]
    # Сервер и обфускация — как в эталоне.
    assert "Endpoint = 203.0.113.10:5858" in cfg["config"]
    assert cfg["server_pub_key"] == "SERVERPUB=" and cfg["Jc"] == "4"
    assert "OWNERPRIV=" not in key and "OWNERPRIV=" not in cfg["config"]
    assert data["hostName"] == "203.0.113.10"


def test_template_container_name():
    assert vks.template_container_name(_template()) == "amnezia-awg2"


def test_next_free_address_skips_server_and_used():
    assert vks.next_free_address(SERVER_CONF) == "10.8.1.2"


def test_remove_peer_keeps_other_sections():
    conf = SERVER_CONF + vks.peer_block("PUB9=", "PSK9=", "10.8.1.9")
    new_conf, found = vks.remove_peer(conf, "PUB9=")
    assert found
    assert "PUB9=" not in new_conf
    assert "OWNERPUB=" in new_conf and "[Interface]" in new_conf
    assert vks.remove_peer(new_conf, "PUB9=")[1] is False


# ── хранилище ───────────────────────────────────────────────────────────────

def test_create_adds_peer_client_row_and_syncs(store):
    result = store.create("Арина Губанова @arriri")
    assert result["peer_id"] == "PUB1="
    docker = store.docker
    assert "PublicKey = PUB1=" in docker.files[CONF_PATH]
    assert "AllowedIPs = 10.8.1.2/32" in docker.files[CONF_PATH]
    clients = json.loads(docker.files[CLIENTS_PATH])
    assert {"OWNERPUB=", "PUB1="} == {c["clientId"] for c in clients}
    assert clients[-1]["userData"]["clientName"] == "Арина Губанова @arriri"
    assert clients[-1]["userData"]["allowed_ips"] == "10.8.1.2/32"
    assert docker.synced == 1
    second = store.create("Второй")
    assert "AllowedIPs = 10.8.1.3/32" in docker.files[CONF_PATH]
    assert second["peer_id"] == "PUB2="


def test_list_returns_only_own_peers_still_in_conf(store):
    store.create("Один")
    store.create("Два")
    # Владелец удалил «Два» из приложения — в конфиге пира нет.
    conf, _ = vks.remove_peer(store.docker.files[CONF_PATH], "PUB2=")
    store.docker.files[CONF_PATH] = conf
    peers = store.list()
    assert [p["peer_id"] for p in peers] == ["PUB1="]  # пир владельца не отдаём


def test_delete_is_idempotent_and_spares_owner(store):
    store.create("Один")
    assert store.delete("PUB1=") is True
    assert "PUB1=" not in store.docker.files[CONF_PATH]
    assert "OWNERPUB=" in store.docker.files[CONF_PATH]
    assert store.delete("PUB1=") is False


# ── HTTP ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def http(store):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), vks.make_handler(store, TOKEN))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _call(base, method, path, body=None, token=TOKEN):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_http_requires_token(http):
    assert _call(http, "GET", "/peers", token=None)[0] == 401
    assert _call(http, "GET", "/peers", token="wrong")[0] == 401
    assert _call(http, "GET", "/health", token=None) == (200, {"ok": True})


def test_http_create_list_delete(http):
    status, body = _call(http, "POST", "/peers", {"label": "Арина"})
    assert status == 201 and body["key"].startswith("vpn://")
    status, body2 = _call(http, "GET", "/peers")
    assert status == 200 and [p["peer_id"] for p in body2["peers"]] == [body["peer_id"]]
    from urllib.parse import quote
    status, body3 = _call(http, "DELETE", "/peers?peer_id=" + quote(body["peer_id"]))
    assert status == 200 and body3 == {"deleted": True}


def test_http_rejects_empty_label(http):
    assert _call(http, "POST", "/peers", {"label": "  "})[0] == 400
