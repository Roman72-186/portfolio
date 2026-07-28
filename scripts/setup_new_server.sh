#!/bin/bash
# Запускать на новом российском сервере: ssh root@NEW_IP 'bash -s' < scripts/setup_new_server.sh
#
# Скрипт идемпотентен: повторный запуск ничего не ломает и не перезаписывает
# уже существующие конфиги. Воспроизводит состояние, которое на старом сервере
# (89.23.96.254) собиралось руками — см. PHASE-3-HANDOFF.md, «Phase 1».
set -e

echo "=== Устанавливаем Docker ==="
if command -v docker >/dev/null 2>&1; then
  echo "  Docker уже установлен: $(docker --version)"
else
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker
  systemctl start docker
  docker --version
fi

echo ""
echo "=== Настраиваем registry-mirrors ==="
# Docker Hub блокирует запросы с российских IP, а Dockerfile тянет python:3.11-slim
# напрямую. Без зеркал первый билд на новом сервере не поднимется.
# Порядок важен: первое зеркало отвечает, остальные — запасные.
if [ -f /etc/docker/daemon.json ]; then
  echo "  /etc/docker/daemon.json уже существует — не трогаем, проверьте вручную:"
  cat /etc/docker/daemon.json
else
  mkdir -p /etc/docker
  cat > /etc/docker/daemon.json <<'JSON'
{
  "registry-mirrors": [
    "https://dockerhub.timeweb.cloud",
    "https://mirror.gcr.io",
    "https://huecker.io"
  ]
}
JSON
  systemctl restart docker
  echo "  Зеркала прописаны, docker перезапущен."
fi

echo ""
echo "=== Проверяем, что образы тянутся ==="
# Проверяем оба источника: прямой Docker Hub (Dockerfile) и зеркало Timeweb (compose).
PULL_OK=1
for IMAGE in python:3.11-slim dockerhub.timeweb.cloud/library/postgres:15-alpine; do
  if docker pull "$IMAGE" >/dev/null 2>&1; then
    echo "  OK: $IMAGE"
  else
    echo "  ПРОВАЛ: $IMAGE не скачивается"
    PULL_OK=0
  fi
done
if [ "$PULL_OK" -eq 0 ]; then
  echo ""
  echo "  ОСТАНОВИТЕСЬ: без образов деплой не поднимется."
  echo "  Подберите рабочее зеркало, впишите в /etc/docker/daemon.json,"
  echo "  выполните systemctl restart docker и запустите этот скрипт заново."
  exit 1
fi

echo ""
echo "=== Swap 1 ГБ ==="
# У app лимит 1400M и 4 uvicorn-воркера; при параллельных загрузках фото пик
# упирается в RAM. Swap — страховка от OOM-kill во время деплоя.
if [ -n "$(swapon --show 2>/dev/null)" ]; then
  echo "  Swap уже активен:"
  swapon --show
else
  fallocate -l 1G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=1024
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  if ! grep -q '^/swapfile' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
  echo "  Swap 1 ГБ подключён и прописан в /etc/fstab."
fi

echo ""
echo "=== UFW: deny incoming, allow 22/80/443 ==="
if ! command -v ufw >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq ufw
fi
# Порядок важен: 22 разрешаем ДО включения, иначе теряем текущую сессию.
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw --force enable >/dev/null
ufw status verbose | head -12

echo ""
echo "=== fail2ban (bantime 24h, maxretry 3) ==="
# Старый сервер банил сотни IP в час — сканеры находят его сразу.
if ! command -v fail2ban-client >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq fail2ban
fi
if [ -f /etc/fail2ban/jail.local ]; then
  echo "  /etc/fail2ban/jail.local уже существует — не трогаем."
else
  # На Ubuntu 24.04 нет /var/log/auth.log, логи sshd идут в journald:
  # без backend=systemd jail молча не стартует.
  SSHD_BACKEND=""
  if [ ! -f /var/log/auth.log ]; then
    apt-get install -y -qq python3-systemd || true
    SSHD_BACKEND="backend = systemd"
  fi
  cat > /etc/fail2ban/jail.local <<CONF
[DEFAULT]
bantime  = 24h
findtime = 1h
maxretry = 3

[sshd]
enabled = true
${SSHD_BACKEND}
CONF
  systemctl enable fail2ban >/dev/null 2>&1 || true
  systemctl restart fail2ban
  echo "  jail.local записан, fail2ban перезапущен."
fi
# После restart сокет fail2ban готов не сразу: без ожидания проверка врёт.
JAIL_OK=0
for _ in $(seq 1 10); do
  if fail2ban-client status sshd >/dev/null 2>&1; then
    JAIL_OK=1
    break
  fi
  sleep 2
done
if [ "$JAIL_OK" -eq 1 ]; then
  echo "  Jail sshd активен."
else
  echo "  ВНИМАНИЕ: jail sshd не поднялся — проверьте: fail2ban-client status"
  echo "  и tail /var/log/fail2ban.log"
fi

echo ""
echo "=== sshd hardening ==="
# Защита от локаута: без рабочего ключа пароль отключать нельзя.
AUTH_KEYS="${HOME:-/root}/.ssh/authorized_keys"
if [ ! -s "$AUTH_KEYS" ]; then
  echo "  ПРОПУСКАЕМ: $AUTH_KEYS пуст или отсутствует."
  echo "  Сначала положите публичный ключ, потом запустите скрипт заново,"
  echo "  иначе после отключения пароля вход будет закрыт."
elif [ -f /etc/ssh/sshd_config.d/00-portfolio-hardening.conf ]; then
  echo "  Конфиг hardening уже на месте — не трогаем."
elif ! grep -qE '^\s*Include\s+/etc/ssh/sshd_config\.d/' /etc/ssh/sshd_config; then
  echo "  ВНИМАНИЕ: sshd_config не подключает /etc/ssh/sshd_config.d/*.conf."
  echo "  Настройте PermitRootLogin/PasswordAuthentication вручную в /etc/ssh/sshd_config."
else
  # Имя с префиксом 00-, потому что sshd берёт ПЕРВОЕ значение параметра,
  # а cloud-init кладёт 50-cloud-init.conf с PasswordAuthentication yes.
  cat > /etc/ssh/sshd_config.d/00-portfolio-hardening.conf <<'CONF'
# Ставится scripts/setup_new_server.sh. Префикс 00- обязателен: sshd применяет
# первое встреченное значение, и этот файл должен опередить 50-cloud-init.conf.
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
MaxAuthTries 3
LoginGraceTime 30
CONF
  # Дополнительно гасим cloud-init, чтобы правило не всплыло при его перегенерации.
  CLOUD_INIT=/etc/ssh/sshd_config.d/50-cloud-init.conf
  if [ -f "$CLOUD_INIT" ] && grep -qi '^\s*PasswordAuthentication' "$CLOUD_INIT"; then
    cp "$CLOUD_INIT" "${CLOUD_INIT}.bak"
    sed -i 's/^\s*PasswordAuthentication/#PasswordAuthentication/I' "$CLOUD_INIT"
    echo "  50-cloud-init.conf: PasswordAuthentication закомментирован (бэкап .bak)."
  fi
  if sshd -t; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd
    echo "  Применено. Эффективные значения:"
    sshd -T | grep -E '^(permitrootlogin|passwordauthentication|maxauthtries|logingracetime) '
    echo "  ПРОВЕРЬТЕ вход по ключу во ВТОРОЙ сессии, не закрывая текущую."
  else
    rm -f /etc/ssh/sshd_config.d/00-portfolio-hardening.conf
    echo "  ПРОВАЛ: sshd -t не принял конфиг, изменения откачены."
  fi
fi

echo ""
echo "=== Создаём директории ==="
mkdir -p /home/portfolio-saas
mkdir -p /root/portfolio-migration
mkdir -p /var/backups/portfolio

echo ""
echo "=== Распаковываем бэкап ==="
# Ожидаем что архив уже загружен в /root/portfolio-backup-*.tar.gz
# (собирается на старом сервере через scripts/make_migration_backup.sh).
ARCHIVE=$(ls -t /root/portfolio-backup-*.tar.gz 2>/dev/null | head -1)
if [ -z "$ARCHIVE" ]; then
  echo "ВНИМАНИЕ: архив не найден в /root/portfolio-backup-*.tar.gz"
  echo "Загрузите архив через WinSCP и запустите:"
  echo "  tar -xzf /root/portfolio-backup-*.tar.gz -C /root/portfolio-migration/"
else
  echo "Распаковываем $ARCHIVE..."
  tar -xzf "$ARCHIVE" -C /root/portfolio-migration/
  if [ -f /root/portfolio-migration/db_dump.sql ]; then
    echo "  db_dump.sql: $(wc -l < /root/portfolio-migration/db_dump.sql) строк"
  else
    echo "  ВНИМАНИЕ: db_dump.sql в архиве нет — restore_db.sh упадёт."
  fi
  # .env в архив намеренно не кладём: source of truth локальный,
  # на сервер его заливает scripts/deploy.py.
fi

echo ""
echo "=== Сертификат Traefik (acme.json) ==="
# Переносим ДО первого старта Traefik: пока DNS смотрит на старый сервер,
# Let's Encrypt не пройдёт challenge, а неудачные попытки жгут rate-limit.
ACME_SRC=/root/portfolio-migration/acme.json
if [ ! -f "$ACME_SRC" ]; then
  echo "  acme.json в архиве нет — пропускаем (сертификат выпустится после DNS switch)."
else
  VOLUME=$(docker volume ls -q --filter name=traefik_certs | head -1)
  if [ -z "$VOLUME" ]; then
    echo "  Volume traefik_certs ещё не создан — Traefik не запускался."
    echo "  Положите файл ПОСЛЕ первого деплоя:"
    echo "    docker run --rm -v <volume>:/le -v /root/portfolio-migration:/src alpine \\"
    echo "      sh -c 'cp /src/acme.json /le/acme.json && chmod 600 /le/acme.json'"
  else
    docker run --rm -v "$VOLUME":/le -v /root/portfolio-migration:/src alpine \
      sh -c 'cp /src/acme.json /le/acme.json && chmod 600 /le/acme.json'
    echo "  acme.json положен в volume $VOLUME (chmod 600)."
  fi
fi

echo ""
echo "=== Ежедневный pg_dump (cron) ==="
if [ -f /etc/cron.d/portfolio-pgbackup ]; then
  echo "  /etc/cron.d/portfolio-pgbackup уже существует — не трогаем."
elif [ -f /root/portfolio-migration/cron-portfolio-pgbackup ]; then
  cp /root/portfolio-migration/cron-portfolio-pgbackup /etc/cron.d/portfolio-pgbackup
  chmod 644 /etc/cron.d/portfolio-pgbackup
  echo "  Восстановлен из архива старого сервера."
else
  # Запасной вариант, если архив собран без cron-файла. Ротация 14 дней.
  cat > /etc/cron.d/portfolio-pgbackup <<'CRON'
# Ежедневный дамп БД portfolio в /var/backups/portfolio, хранение 14 дней.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
30 3 * * * root /usr/bin/docker exec portfolio-saas-db-1 pg_dump -U portfolio -d portfolio --clean --if-exists | gzip > /var/backups/portfolio/portfolio-$(date +\%Y\%m\%dT\%H\%M\%SZ).sql.gz && find /var/backups/portfolio -name 'portfolio-*.sql.gz' -mtime +14 -delete
CRON
  chmod 644 /etc/cron.d/portfolio-pgbackup
  echo "  Записан дефолтный cron (03:30, ротация 14 дней)."
fi

echo ""
echo "=== Права на .env ==="
chmod 600 /home/portfolio-saas/.env* 2>/dev/null || true
ls -la /home/portfolio-saas/.env* 2>/dev/null || echo "  .env пока нет — появится после deploy.py."

echo ""
echo "=== Готово ==="
echo "Следующие шаги:"
echo "  1. Проверьте вход по SSH-ключу во второй сессии (не закрывая текущую)."
echo "  2. Запустите deploy.py с новым PORTFOLIO_SSH_HOST"
echo "     (первый раз: PORTFOLIO_SSH_ALLOW_UNKNOWN_HOST=1, потом ssh-keyscan в deploy_known_hosts)."
echo "  3. Если acme.json ждал volume — положите его сейчас и перезапустите traefik."
echo "  4. Запустите scripts/restore_db.sh"
