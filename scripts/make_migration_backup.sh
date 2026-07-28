#!/bin/bash
# Запускать на СТАРОМ сервере (89.23.96.254): ssh root@OLD_IP 'bash -s' < scripts/make_migration_backup.sh
#
# Собирает /root/portfolio-backup-<TS>.tar.gz в формате, который ждёт
# scripts/setup_new_server.sh: файлы лежат в корне архива и распаковываются
# в /root/portfolio-migration/.
#
# Переменные:
#   SKIP_SERVER_BACKUPS=1  — не класть в архив старые дампы и точки отката
#   SKIP_FITNESS=1         — не забирать проект fitness-dating
#
# ВАЖНО: .env приложения в архив не попадает. Его source of truth локальный,
# на новый сервер он уезжает через scripts/deploy.py.
set -e

TS=$(date -u +%Y%m%dT%H%M%SZ)
STAGE="/root/portfolio-migration-src-$TS"
ARCHIVE="/root/portfolio-backup-$TS.tar.gz"
DB_CONTAINER="portfolio-saas-db-1"

mkdir -p "$STAGE"
trap 'rm -rf "$STAGE"' EXIT

echo "=== Проверяем место на диске ==="
FREE_MB=$(df -Pm /root | awk 'NR==2 {print $4}')
echo "  Свободно: ${FREE_MB} МБ"
if [ "$FREE_MB" -lt 2048 ]; then
  echo "  ОСТАНОВИТЕСЬ: меньше 2 ГБ свободно, архив может не собраться."
  echo "  Освободите место (docker builder prune -af) и запустите заново."
  exit 1
fi

echo ""
echo "=== Дамп PostgreSQL ==="
if ! docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
  echo "  ОШИБКА: контейнер $DB_CONTAINER не запущен."
  exit 1
fi
docker exec "$DB_CONTAINER" pg_dump -U portfolio -d portfolio --clean --if-exists \
  > "$STAGE/db_dump.sql"
echo "  db_dump.sql: $(du -h "$STAGE/db_dump.sql" | cut -f1), $(wc -l < "$STAGE/db_dump.sql") строк"
# Пустой или подозрительно короткий дамп лучше поймать здесь, а не на новом сервере.
if [ "$(wc -l < "$STAGE/db_dump.sql")" -lt 100 ]; then
  echo "  ОСТАНОВИТЕСЬ: дамп слишком короткий, что-то пошло не так."
  exit 1
fi

echo ""
echo "=== Сертификат Traefik (acme.json) ==="
# Нужен, чтобы на новом сервере был валидный сертификат ДО переключения DNS.
VOLUME=$(docker volume ls -q --filter name=traefik_certs | head -1)
if [ -z "$VOLUME" ]; then
  echo "  ВНИМАНИЕ: volume traefik_certs не найден, пропускаем."
else
  docker run --rm -v "$VOLUME":/le -v "$STAGE":/out alpine \
    sh -c 'cp /le/acme.json /out/acme.json' 2>/dev/null || true
  if [ -f "$STAGE/acme.json" ]; then
    echo "  acme.json забран из volume $VOLUME ($(du -h "$STAGE/acme.json" | cut -f1))"
  else
    echo "  ВНИМАНИЕ: acme.json в volume $VOLUME не нашёлся."
  fi
fi

echo ""
echo "=== cron ежедневного pg_dump ==="
if [ -f /etc/cron.d/portfolio-pgbackup ]; then
  cp /etc/cron.d/portfolio-pgbackup "$STAGE/cron-portfolio-pgbackup"
  echo "  Забран."
else
  echo "  ВНИМАНИЕ: /etc/cron.d/portfolio-pgbackup нет — на новом сервере встанет дефолтный."
fi

echo ""
echo "=== Точки отката и старые дампы ==="
if [ "${SKIP_SERVER_BACKUPS:-0}" = "1" ]; then
  echo "  Пропущено (SKIP_SERVER_BACKUPS=1)."
else
  mkdir -p "$STAGE/server-backups"
  for DIR in /var/backups/portfolio /home/portfolio-saas/backups; do
    if [ -d "$DIR" ]; then
      NAME=$(echo "$DIR" | tr '/' '_' | sed 's/^_//')
      tar -czf "$STAGE/server-backups/$NAME.tar.gz" -C "$(dirname "$DIR")" "$(basename "$DIR")"
      echo "  $DIR -> $NAME.tar.gz ($(du -h "$STAGE/server-backups/$NAME.tar.gz" | cut -f1))"
    else
      echo "  $DIR — нет, пропускаем."
    fi
  done
  # В /home/portfolio-saas/backups лежит restore SQL для month-shift от 2026-05-01
  # (docs/ops-history.md). Единственный способ откатить ту операцию — не терять.
fi

echo ""
echo "=== Проект fitness-dating ==="
if [ "${SKIP_FITNESS:-0}" = "1" ]; then
  echo "  Пропущено (SKIP_FITNESS=1)."
elif ! docker ps -a --format '{{.Names}}' | grep -qx fm_db; then
  echo "  Контейнер fm_db не найден — пропускаем."
else
  mkdir -p "$STAGE/fitness-dating"
  # Пользователя БД берём из окружения контейнера: имя роли здесь не угадать.
  # Сбой здесь не должен ронять весь архив — portfolio-saas важнее.
  if docker exec fm_db sh -c 'pg_dumpall -U "$POSTGRES_USER"' > "$STAGE/fitness-dating/fm_db_dumpall.sql"; then
    echo "  fm_db_dumpall.sql: $(du -h "$STAGE/fitness-dating/fm_db_dumpall.sql" | cut -f1)"
  else
    echo "  ВНИМАНИЕ: дамп fm_db не снялся, заберите вручную."
  fi

  # Каталог проекта — из label docker compose, вручную путь не угадывается.
  FM_DIR=$(docker inspect fm_app \
    --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null || true)
  if [ -n "$FM_DIR" ] && [ -d "$FM_DIR" ]; then
    tar -czf "$STAGE/fitness-dating/project.tar.gz" -C "$(dirname "$FM_DIR")" "$(basename "$FM_DIR")"
    echo "  Каталог $FM_DIR -> project.tar.gz ($(du -h "$STAGE/fitness-dating/project.tar.gz" | cut -f1))"
    echo "  ВНИМАНИЕ: внутри лежит .env проекта — архив содержит секреты."
  else
    echo "  ВНИМАНИЕ: каталог проекта не определился по label, заберите вручную."
  fi
  docker inspect fm_app fm_db > "$STAGE/fitness-dating/containers-inspect.json" 2>/dev/null || true
fi

echo ""
echo "=== Инвентарь S3 ==="
# Нужен для этапа 2 (переезд S3) и сверки после rclone sync.
if command -v rclone >/dev/null 2>&1 && [ -n "$(rclone listremotes 2>/dev/null)" ]; then
  REMOTE=$(rclone listremotes | head -1)
  BUCKET="${S3_BUCKET_NAME:-}"
  if [ -z "$BUCKET" ]; then
    echo "  rclone есть (remote $REMOTE), но бакет не задан."
    echo "  Запустите отдельно: rclone lsf -R ${REMOTE}BUCKET > s3-inventory-before.txt"
  else
    rclone lsf -R "${REMOTE}${BUCKET}" > "$STAGE/s3-inventory-before.txt" || true
    rclone size "${REMOTE}${BUCKET}" > "$STAGE/s3-size-before.txt" 2>&1 || true
    echo "  Инвентарь снят: $(wc -l < "$STAGE/s3-inventory-before.txt") объектов."
  fi
else
  echo "  rclone не настроен — пропускаем (этап 2 всё равно идёт отдельно)."
fi

echo ""
echo "=== Справка о состоянии сервера ==="
{
  echo "# Снято $TS на $(hostname) ($(hostname -I | awk '{print $1}'))"
  echo ""
  echo "## docker ps"
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
  echo ""
  echo "## docker volume ls"
  docker volume ls
  echo ""
  echo "## df -h"
  df -h /
  echo ""
  echo "## Размер БД"
  docker exec "$DB_CONTAINER" psql -U portfolio -d portfolio -tAc \
    "select pg_size_pretty(pg_database_size('portfolio'))" 2>/dev/null || true
  echo ""
  echo "## Ссылки на S3 в БД (для сверки после restore и после этапа 2)"
  # Шесть колонок в пяти таблицах — столько же, сколько переписывает
  # scripts/migrate_s3_urls.sql. План от 11.07 знал только про четыре.
  docker exec "$DB_CONTAINER" psql -U portfolio -d portfolio -tAc "
    select 'works.s3_url=' || count(*) from works where s3_url is not null
    union all select 'feedback_photos.s3_url=' || count(*) from feedback_photos where s3_url is not null
    union all select 'feedback_messages.photo_s3_url=' || count(*) from feedback_messages where photo_s3_url is not null
    union all select 'feedback_messages.video_s3_url=' || count(*) from feedback_messages where video_s3_url is not null
    union all select 'exam_tickets.image_s3_url=' || count(*) from exam_tickets where image_s3_url is not null
    union all select 'legacy_portfolio_photos.s3_url=' || count(*) from legacy_portfolio_photos where s3_url is not null
  " 2>/dev/null || true
} > "$STAGE/server-state.txt"
cat "$STAGE/server-state.txt"

echo ""
echo "=== Собираем архив ==="
tar -czf "$ARCHIVE" -C "$STAGE" .
chmod 600 "$ARCHIVE"
echo "  $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
sha256sum "$ARCHIVE" | tee "${ARCHIVE}.sha256"

echo ""
echo "=== Готово ==="
echo "Дальше:"
echo "  1. Скачайте архив и .sha256, сверьте контрольную сумму на новом сервере."
echo "  2. Положите архив в /root/ нового сервера."
echo "  3. Запустите там scripts/setup_new_server.sh."
echo "  4. После успешной миграции удалите архив с обоих серверов:"
echo "     он содержит дамп БД и, если забирали fitness-dating, чужие секреты."
