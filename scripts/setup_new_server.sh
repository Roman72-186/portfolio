#!/bin/bash
# Запускать на новом российском сервере: ssh root@NEW_IP 'bash -s' < scripts/setup_new_server.sh
set -e

echo "=== Устанавливаем Docker ==="
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker
docker --version

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
