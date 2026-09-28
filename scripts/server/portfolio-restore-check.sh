#!/bin/bash
# Проверка, что дамп базы Apparchi действительно восстанавливается
# (код-ревью 28.09.2026, раздел «Бэкапы»: на этом сервере ни разу не
# проверялось).
#
# Разворачивает дамп в одноразовый контейнер Postgres без сети, сверяет число
# строк в ключевых таблицах с живой базой и удаляет контейнер. Живую базу
# только читает. Запуск на сервере под root:
#   bash -s < scripts/server/portfolio-restore-check.sh            # свежий ночной дамп
#   bash -s -- /var/backups/portfolio/<файл>.sql.gz < scripts/...  # конкретный
#
# Цифры «дамп» и «живая» расходятся на то, что появилось после снятия дампа:
# это нормально. Тревога — пустая таблица в дампе или ошибка восстановления.
set -euo pipefail

DUMP=${1:-$(ls -1t /var/backups/portfolio/portfolio-*.sql.gz | head -1)}
NAME=portfolio-restore-check
IMAGE=dockerhub.timeweb.cloud/library/postgres:15-alpine
LIVE=portfolio-saas-db-1
TABLES="users works feedbacks feedback_messages exam_cycles tracker_tasks task_blocks task_block_answers task_block_submissions"

echo "Дамп: $DUMP ($(stat -c %s "$DUMP") байт)"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --network none \
    -e POSTGRES_USER=portfolio -e POSTGRES_PASSWORD=restore-check -e POSTGRES_DB=portfolio \
    "$IMAGE" >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

for _ in $(seq 1 30); do
    docker exec "$NAME" pg_isready -U portfolio -d portfolio >/dev/null 2>&1 && break
    sleep 2
done
# Образ поднимает сервер дважды (init, затем основной) — ждём основной.
sleep 3
docker exec "$NAME" pg_isready -U portfolio -d portfolio >/dev/null

gunzip -c "$DUMP" | docker exec -i "$NAME" psql -U portfolio -d portfolio -v ON_ERROR_STOP=1 -q >/dev/null
echo "Восстановление прошло без ошибок."

status=0
for t in $TABLES; do
    restored=$(docker exec "$NAME" psql -U portfolio -d portfolio -tAc "select count(*) from $t")
    live=$(docker exec "$LIVE" psql -U portfolio -d portfolio -tAc "select count(*) from $t")
    printf '%-24s дамп=%-8s живая=%s\n' "$t" "$restored" "$live"
    if [ "$restored" -eq 0 ] && [ "$live" -gt 0 ]; then status=1; fi
done
exit $status
