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

COMPOSE="docker-compose.prod-ru.yml"

echo "=== Ждём готовности PostgreSQL ==="
until docker exec portfolio-saas-db-1 pg_isready -U portfolio -d portfolio 2>/dev/null; do
  echo "  ...ожидание..."
  sleep 3
done
echo "  PostgreSQL готов."

echo ""
echo "=== Останавливаем app ==="
# Дамп идёт с --clean --if-exists, то есть начинается с DROP TABLE. Четыре uvicorn-воркера
# держат открытые коннекты (пул 10+5), плюс lifespan гонит alembic и seed ролей —
# DROP повиснет на блокировке или упадёт. Гасим app на время восстановления.
cd "$REMOTE_DIR" && docker compose -f "$COMPOSE" stop app
echo "  app остановлен."

echo ""
echo "=== Восстанавливаем дамп ==="
docker exec -i portfolio-saas-db-1 psql -U portfolio -d portfolio < "$DUMP"

echo ""
echo "=== Поднимаем app обратно ==="
cd "$REMOTE_DIR" && docker compose -f "$COMPOSE" up -d app

echo "  Ждём, пока app начнёт отвечать..."
for _ in $(seq 1 30); do
  if docker compose -f "$COMPOSE" exec -T app python -c "" 2>/dev/null; then
    break
  fi
  sleep 2
done

echo ""
echo "=== Применяем миграции (на всякий случай) ==="
cd "$REMOTE_DIR" && docker compose -f "$COMPOSE" exec -T app \
  alembic upgrade head 2>/dev/null || true

echo ""
echo "✓ БД восстановлена."
echo "  Проверка: docker exec portfolio-saas-db-1 psql -U portfolio -c '\dt'"
echo "  Логи app: docker compose -f $COMPOSE logs --tail=50 app"
