-- Переписывание публичных S3-ссылок в БД. Этап 2 миграции (переезд S3),
-- выполняется отдельно от переезда VDS.
--
-- Запуск (сначала обязательно на копии БД):
--   docker exec -i portfolio-saas-db-1 psql -U portfolio -d portfolio \
--     -v old_prefix='https://s3.timeweb.cloud/OLD_BUCKET/' \
--     -v new_prefix='https://s3.ru-1.storage.selcloud.ru/NEW_BUCKET/' \
--     < scripts/migrate_s3_urls.sql
--
-- Префиксы обязаны заканчиваться слэшем: URL строится как
-- {endpoint}/{bucket}/{s3_path} (app/services/s3.py:126, path-style).
--
-- Колонки s3_path НЕ трогаем: там путь внутри бакета, без endpoint и имени бакета.
--
-- Скрипт идемпотентен: после замены строки перестают совпадать со старым
-- префиксом, повторный запуск ничего не изменит.

\set ON_ERROR_STOP on

\if :{?old_prefix}
\else
\echo 'ОШИБКА: не задан -v old_prefix'
\quit
\endif
\if :{?new_prefix}
\else
\echo 'ОШИБКА: не задан -v new_prefix'
\quit
\endif

\echo ''
\echo '=== ДО замены ==='
SELECT 'works.s3_url'                    AS target, count(*) AS old_prefix_rows FROM works               WHERE starts_with(s3_url, :'old_prefix')
UNION ALL SELECT 'feedback_photos.s3_url',          count(*) FROM feedback_photos     WHERE starts_with(s3_url, :'old_prefix')
UNION ALL SELECT 'feedback_messages.photo_s3_url',  count(*) FROM feedback_messages   WHERE starts_with(photo_s3_url, :'old_prefix')
UNION ALL SELECT 'feedback_messages.video_s3_url',  count(*) FROM feedback_messages   WHERE starts_with(video_s3_url, :'old_prefix')
UNION ALL SELECT 'exam_tickets.image_s3_url',       count(*) FROM exam_tickets        WHERE starts_with(image_s3_url, :'old_prefix')
UNION ALL SELECT 'legacy_portfolio_photos.s3_url',  count(*) FROM legacy_portfolio_photos WHERE starts_with(s3_url, :'old_prefix');

\echo ''
\echo '=== Ссылки, не подходящие ни под старый, ни под новый префикс ==='
\echo '(если тут не ноль — разберитесь ДО замены: это чужие или битые URL)'
SELECT 'works.s3_url' AS target, count(*) AS foreign_rows FROM works
  WHERE s3_url IS NOT NULL AND NOT starts_with(s3_url, :'old_prefix') AND NOT starts_with(s3_url, :'new_prefix')
UNION ALL SELECT 'feedback_photos.s3_url', count(*) FROM feedback_photos
  WHERE s3_url IS NOT NULL AND NOT starts_with(s3_url, :'old_prefix') AND NOT starts_with(s3_url, :'new_prefix')
UNION ALL SELECT 'feedback_messages.photo_s3_url', count(*) FROM feedback_messages
  WHERE photo_s3_url IS NOT NULL AND NOT starts_with(photo_s3_url, :'old_prefix') AND NOT starts_with(photo_s3_url, :'new_prefix')
UNION ALL SELECT 'feedback_messages.video_s3_url', count(*) FROM feedback_messages
  WHERE video_s3_url IS NOT NULL AND NOT starts_with(video_s3_url, :'old_prefix') AND NOT starts_with(video_s3_url, :'new_prefix')
UNION ALL SELECT 'exam_tickets.image_s3_url', count(*) FROM exam_tickets
  WHERE image_s3_url IS NOT NULL AND NOT starts_with(image_s3_url, :'old_prefix') AND NOT starts_with(image_s3_url, :'new_prefix')
UNION ALL SELECT 'legacy_portfolio_photos.s3_url', count(*) FROM legacy_portfolio_photos
  WHERE s3_url IS NOT NULL AND NOT starts_with(s3_url, :'old_prefix') AND NOT starts_with(s3_url, :'new_prefix');

BEGIN;

UPDATE works
   SET s3_url = :'new_prefix' || substring(s3_url from length(:'old_prefix') + 1)
 WHERE starts_with(s3_url, :'old_prefix');

UPDATE feedback_photos
   SET s3_url = :'new_prefix' || substring(s3_url from length(:'old_prefix') + 1)
 WHERE starts_with(s3_url, :'old_prefix');

UPDATE feedback_messages
   SET photo_s3_url = :'new_prefix' || substring(photo_s3_url from length(:'old_prefix') + 1)
 WHERE starts_with(photo_s3_url, :'old_prefix');

UPDATE feedback_messages
   SET video_s3_url = :'new_prefix' || substring(video_s3_url from length(:'old_prefix') + 1)
 WHERE starts_with(video_s3_url, :'old_prefix');

UPDATE exam_tickets
   SET image_s3_url = :'new_prefix' || substring(image_s3_url from length(:'old_prefix') + 1)
 WHERE starts_with(image_s3_url, :'old_prefix');

UPDATE legacy_portfolio_photos
   SET s3_url = :'new_prefix' || substring(s3_url from length(:'old_prefix') + 1)
 WHERE starts_with(s3_url, :'old_prefix');

COMMIT;

\echo ''
\echo '=== ПОСЛЕ замены ==='
\echo '(old должен быть 0, new — совпадать с тем, что было old до запуска)'
SELECT 'works.s3_url' AS target,
       count(*) FILTER (WHERE starts_with(s3_url, :'old_prefix')) AS old_rows,
       count(*) FILTER (WHERE starts_with(s3_url, :'new_prefix')) AS new_rows
  FROM works
UNION ALL SELECT 'feedback_photos.s3_url',
       count(*) FILTER (WHERE starts_with(s3_url, :'old_prefix')),
       count(*) FILTER (WHERE starts_with(s3_url, :'new_prefix')) FROM feedback_photos
UNION ALL SELECT 'feedback_messages.photo_s3_url',
       count(*) FILTER (WHERE starts_with(photo_s3_url, :'old_prefix')),
       count(*) FILTER (WHERE starts_with(photo_s3_url, :'new_prefix')) FROM feedback_messages
UNION ALL SELECT 'feedback_messages.video_s3_url',
       count(*) FILTER (WHERE starts_with(video_s3_url, :'old_prefix')),
       count(*) FILTER (WHERE starts_with(video_s3_url, :'new_prefix')) FROM feedback_messages
UNION ALL SELECT 'exam_tickets.image_s3_url',
       count(*) FILTER (WHERE starts_with(image_s3_url, :'old_prefix')),
       count(*) FILTER (WHERE starts_with(image_s3_url, :'new_prefix')) FROM exam_tickets
UNION ALL SELECT 'legacy_portfolio_photos.s3_url',
       count(*) FILTER (WHERE starts_with(s3_url, :'old_prefix')),
       count(*) FILTER (WHERE starts_with(s3_url, :'new_prefix')) FROM legacy_portfolio_photos;

\echo ''
\echo 'Готово. Дальше: обновить S3_* в .env, задеплоить, открыть старое фото в интерфейсе.'
