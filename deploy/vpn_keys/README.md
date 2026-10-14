# vpn-keys — выдача ключей AmneziaWG ученикам

Служба на VPN-сервере в Нидерландах. Платформа зовёт её из `app/services/vpn_keys.py`,
бот отдаёт ключ по `/vpn`. План — `../../../plans/2026-10-13-vpn-amnezia.md`.
Не выкатывается через `scripts/deploy.py`: тот ходит только на сервер приложения.

## Установка (один раз)

1. Владелец ставит AmneziaWG из своего AmneziaVPN («Свой сервер», SSH-ключ),
   меняет порт VPN на число до 9999 и выпускает гостевой ключ-эталон
   («Поделиться» → «Поделиться VPN», протокол AmneziaWG).
2. Посмотреть, где конфиг в контейнере, и сверить с переменными ниже:
   `docker ps` (имя, обычно `amnezia-awg2`), `docker exec amnezia-awg2 ls /opt/amnezia/awg`.
3. Файлы:
   ```
   install -d -m 700 /etc/vpn-keys /opt/vpn-keys
   install -m 644 vpn_keys_server.py /opt/vpn-keys/
   install -m 644 vpn-keys.service /etc/systemd/system/
   printf '%s' 'vpn://…эталон…' > /etc/vpn-keys/template.vpn && chmod 600 /etc/vpn-keys/template.vpn
   openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
     -subj "/CN=<IP сервера>" -addext "subjectAltName=IP:<IP сервера>" \
     -keyout /etc/vpn-keys/key.pem -out /etc/vpn-keys/cert.pem
   ```
4. `/etc/vpn-keys/env` (права 600):
   ```
   VPN_KEYS_TOKEN=<openssl rand -hex 32>
   VPN_KEYS_CERT=/etc/vpn-keys/cert.pem
   VPN_KEYS_KEY=/etc/vpn-keys/key.pem
   VPN_KEYS_PORT=8443
   VPN_KEYS_STATE=/var/lib/vpn-keys/peers.json
   VPN_KEYS_LOCK=/run/vpn-keys/lock
   VPN_KEYS_NAME=Код архитектора   # имя сервера в приложении ученика
   # если отличаются от умолчаний:
   # VPN_KEYS_CONF=/opt/amnezia/awg/awg0.conf  (AmneziaWG 3.1; у старых — wg0.conf)
   # VPN_KEYS_CLIENTS=/opt/amnezia/awg/clientsTable
   # VPN_KEYS_CONTAINER=amnezia-awg2   (по умолчанию — из эталона)
   # VPN_KEYS_TOOL=awg                 (в старых контейнерах — wg)
   ```
5. Порт службы — только с сервера приложения: `ufw allow from 139.100.237.57 to any port 8443 proto tcp`.
6. `systemctl daemon-reload && systemctl enable --now vpn-keys`, журнал — `journalctl -t vpn-keys`.
   На старте служба читает конфиг и собирает пробный ключ: ошибка в путях видна сразу.

## Что вносит владелец в `.env.prod` приложения

```
VPN_KEYS_URL=https://<IP сервера>:8443
VPN_KEYS_TOKEN=<тот же токен>
VPN_KEYS_CERT_B64=<base64 -w0 /etc/vpn-keys/cert.pem>   # приложение доверяет только ему
```
Сертификат самоподписанный и публичный, одной строкой — чтобы не монтировать файл
в контейнер приложения.
Пусто — бот отвечает «пока недоступно», ничего не ломается.

## Откат

`systemctl disable --now vpn-keys` и снять `VPN_KEYS_URL` — бот перестанет выдавать
ключи. Выданные ключи работают, пока пиры стоят в конфиге Amnezia; удалить их можно
из приложения владельца.
