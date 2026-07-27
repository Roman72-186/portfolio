#!/bin/bash
# Запускать на новом российском сервере: ssh root@NEW_IP 'bash -s' < scripts/setup_new_server.sh
set -e

echo "=== Устанавливаем Docker ==="
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker
docker --version

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
echo "=== Создаём директории ==="
mkdir -p /home/portfolio-saas
mkdir -p /root/portfolio-migration

echo ""
echo "=== Распаковываем бэкап ==="
# Ожидаем что архив уже загружен в /root/portfolio-backup-*.tar.gz
ARCHIVE=$(ls /root/portfolio-backup-*.tar.gz 2>/dev/null | head -1)
if [ -z "$ARCHIVE" ]; then
  echo "ВНИМАНИЕ: архив не найден в /root/portfolio-backup-*.tar.gz"
  echo "Загрузите архив через WinSCP и запустите:"
  echo "  tar -xzf /root/portfolio-backup-*.tar.gz -C /root/portfolio-migration/"
else
  echo "Распаковываем $ARCHIVE..."
  tar -xzf "$ARCHIVE" -C /root/portfolio-migration/
  echo "  db_dump.sql: $(wc -l < /root/portfolio-migration/db_dump.sql) строк"
  echo "  portfolio.env: OK"
fi

echo ""
echo "=== Готово ==="
echo "Следующие шаги:"
echo "  1. Запустите deploy.py с новым PORTFOLIO_SSH_HOST"
echo "  2. Запустите scripts/restore_db.sh"
