#!/usr/bin/env bash
# Прогон кабинетных маршрутов под сессией суперадмина.
# Секреты берутся из .env на сервере и наружу не печатаются.
set -u

cd /home/portfolio-saas 2>/dev/null || cd /root/portfolio-saas || exit 1

KEY=$(grep -m1 '^ADMIN_ACCESS_TOKEN=' .env | cut -d= -f2- | tr -d '"'"'"'\r')
if [ -z "$KEY" ]; then echo "ADMIN_ACCESS_TOKEN не найден в .env"; exit 1; fi

JAR=$(mktemp)
H='Host: apparchi.ru'
B='https://127.0.0.1'

code() { curl -sk -o /dev/null -w '%{http_code}' -H "$H" -b "$JAR" -c "$JAR" "$B$1"; }

# вход
LOGIN=$(curl -sk -o /dev/null -w '%{http_code}' -H "$H" -c "$JAR" "$B/auth/admin-access?key=$KEY")
echo "auth/admin-access: $LOGIN"
if ! grep -q session "$JAR" 2>/dev/null; then echo "СЕССИЯ НЕ ВЫДАНА - дальше смысла нет"; rm -f "$JAR"; exit 1; fi

for p in \
  /cabinet \
  /cabinet/profile \
  /cabinet/notifications \
  /cabinet/portfolio \
  /cabinet/cycle \
  /cabinet/curator \
  /cabinet/curator/reports \
  /cabinet/curator/portfolio \
  /cabinet/curator/mock-exams \
  /cabinet/curator/retakes \
  /cabinet/admin-panel \
  /cabinet/admin/students \
  /cabinet/admin/mock-check \
  /cabinet/staff/cycles \
  /cabinet/feedback/ \
  /gallery \
  /3dlab \
  /cabinet/3dlab/enter
do
  printf '%-34s %s\n' "$p" "$(code "$p")"
done

echo "--- S3: доступность боевых файлов ---"
for col in "works s3_url" "legacy_portfolio_photos s3_url" "feedback_messages photo_s3_url" "exam_tickets image_s3_url"; do
  set -- $col
  URL=$(docker exec portfolio-saas-db-1 psql -U portfolio -d portfolio -t -A \
        -c "select $2 from $1 where $2 is not null limit 1" 2>/dev/null | tr -d '\r')
  if [ -n "$URL" ]; then
    printf '%-28s %s\n' "$1.$2" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL")"
  else
    printf '%-28s %s\n' "$1.$2" "нет данных"
  fi
done

echo "--- внутренние эндпоинты ---"
INT=$(grep -m1 '^INTERNAL_API_TOKEN=' .env | cut -d= -f2- | tr -d '"'"'"'\r')
LAB=$(grep -m1 '^LAB3D_INTERNAL_TOKEN=' .env | cut -d= -f2- | tr -d '"'"'"'\r')
# оба эндпоинта читают один и тот же заголовок X-Internal-Token, но сверяют с разными секретами
[ -n "$INT" ] && echo "issue-link (валидный токен): $(curl -sk -o /dev/null -w '%{http_code}' -X POST -H "$H" -H "Content-Type: application/json" -H "X-Internal-Token: $INT" -d '{"vk_id":1}' "$B/auth/internal/issue-link")" || echo "issue-link: INTERNAL_API_TOKEN в .env пуст -> эндпоинт всегда 503"
[ -n "$LAB" ] && echo "sso/verify (валидный токен):  $(curl -sk -o /dev/null -w '%{http_code}' -X POST -H "$H" -H "Content-Type: application/json" -H "X-Internal-Token: $LAB" -d '{"token":"probe"}' "$B/auth/internal/sso/verify")" || echo "sso/verify: LAB3D_INTERNAL_TOKEN в .env пуст -> эндпоинт всегда 503"

rm -f "$JAR"
