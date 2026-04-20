#!/bin/bash
# Запускать на новом сервере ПОСЛЕ первого docker compose up
# ssh root@NEW_IP 'bash -s' < scripts/restore_db.sh
set -e

REMOTE_DIR="/home/portfolio-saas"
DUMP="/root/portfolio-migration/db_dump.sql"

if [ ! -f "$DUMP" ]; then
  echo "ОШИБКА: дамп не найден: $DUMP"
  echo "Сначала запустите setup_new_server.sh и убедитесь что архив распакован."
  exit 1
fi

echo "=== Ждём готовности PostgreSQL ==="
until docker exec portfolio-saas-db-1 pg_isready -U portfolio -d portfolio 2>/dev/null; do
  echo "  ...ожидание..."
  sleep 3
done
echo "  PostgreSQL готов."

echo ""
echo "=== Восстанавливаем дамп ==="
docker exec -i portfolio-saas-db-1 psql -U portfolio -d portfolio < "$DUMP"

echo ""
echo "=== Применяем миграции (на всякий случай) ==="
cd "$REMOTE_DIR" && docker compose -f docker-compose.prod-ru.yml exec -T app \
  alembic upgrade head 2>/dev/null || true

echo ""
echo "✓ БД восстановлена."
echo "  Проверка: docker exec portfolio-saas-db-1 psql -U portfolio -c '\dt'"
