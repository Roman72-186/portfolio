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

# Защита от главной ошибки cutover: свежий архив не приехал, и скрипт молча
# восстанавливает вчерашний дамп поверх свежих данных. Возраст считаем в минутах.
DUMP_MAX_AGE_MIN="${DUMP_MAX_AGE_MIN:-180}"
DUMP_AGE_MIN=$(( ( $(date +%s) - $(stat -c %Y "$DUMP") ) / 60 ))
echo "=== Дамп ==="
echo "  файл:   $DUMP"
echo "  снят:   $(date -u -d "@$(stat -c %Y "$DUMP")" '+%Y-%m-%d %H:%M:%SZ') (${DUMP_AGE_MIN} мин назад)"
echo "  строк:  $(wc -l < "$DUMP")"
if [ "$DUMP_AGE_MIN" -gt "$DUMP_MAX_AGE_MIN" ]; then
  echo ""
  echo "  ОСТАНОВЛЕНО: дампу ${DUMP_AGE_MIN} мин, порог ${DUMP_MAX_AGE_MIN} мин."
  echo "  Похоже, свежий архив с боевого сервера не приехал и распакован старый."
  echo "  Если это осознанно (тестовый прогон), запустите с DUMP_MAX_AGE_MIN=100000."
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
# ON_ERROR_STOP не ставим намеренно: часть ошибок дампа безобидна (роли, владельцы),
# а обрыв на середине оставит БД в полусостоянии. Вместо этого пишем весь вывод
# в лог и считаем ошибки — молчаливого «успеха» больше нет.
RESTORE_LOG="/root/portfolio-migration/restore-$(date -u +%Y%m%dT%H%M%SZ).log"
docker exec -i portfolio-saas-db-1 psql -U portfolio -d portfolio \
  < "$DUMP" > "$RESTORE_LOG" 2>&1 || true
ERRORS=$(grep -c '^ERROR:' "$RESTORE_LOG" || true)
echo "  лог: $RESTORE_LOG"
echo "  строк ERROR: $ERRORS"
if [ "$ERRORS" != "0" ]; then
  echo "  Первые ошибки:"
  grep '^ERROR:' "$RESTORE_LOG" | head -20 | sed 's/^/    /'
  echo "  ВНИМАНИЕ: восстановление прошло с ошибками — сверьте счётчики ниже до строки."
fi

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
echo "=== Счётчики (сверить со server-state.txt из архива, до строки) ==="
# Пять основных таблиц и все шесть колонок с S3-ссылками. Сверка обязательна:
# psql выше не останавливается на ошибке, поэтому «успех» сам по себе ничего не значит.
docker exec portfolio-saas-db-1 psql -U portfolio -d portfolio -tAc "
  select 'users=' || count(*) from users
  union all select 'works=' || count(*) from works
  union all select 'feedbacks=' || count(*) from feedbacks
  union all select 'exam_assignments=' || count(*) from exam_assignments
  union all select 'legacy_portfolio_photos=' || count(*) from legacy_portfolio_photos
  union all select 'works.s3_url=' || count(*) from works where s3_url is not null
  union all select 'feedback_photos.s3_url=' || count(*) from feedback_photos where s3_url is not null
  union all select 'feedback_messages.photo_s3_url=' || count(*) from feedback_messages where photo_s3_url is not null
  union all select 'feedback_messages.video_s3_url=' || count(*) from feedback_messages where video_s3_url is not null
  union all select 'exam_tickets.image_s3_url=' || count(*) from exam_tickets where image_s3_url is not null
  union all select 'legacy_portfolio_photos.s3_url=' || count(*) from legacy_portfolio_photos where s3_url is not null
" 2>/dev/null | sed 's/^/  /' || echo "  ВНИМАНИЕ: счётчики не снялись, снимите руками."

echo ""
echo "✓ БД восстановлена."
echo "  Проверка: docker exec portfolio-saas-db-1 psql -U portfolio -c '\dt'"
echo "  Логи app: docker compose -f $COMPOSE logs --tail=50 app"
