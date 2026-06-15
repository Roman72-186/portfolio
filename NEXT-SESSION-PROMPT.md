# Промт для нового чата

Продолжаем расследование бага: при сдаче пробника (загрузка фото) у части учеников
появляется ошибка "сессия истекла/не найдена", но фото при этом ФАКТИЧЕСКИ загружаются
(видны во вкладке "Пробники" на карточке ученика), при этом создаётся цикл ExamCycle.

## Что уже сделано (задеплоено на прод 89.23.96.254)

1. **Исправлен компаундинг-баг** в `app/api/upload.py::_locked_mock_subjects` — раньше
   ЛЮБОЙ открытый цикл на активный билет блокировал предмет ("работа сдана, ждите ОС"),
   даже если в цикле были только этапные фото без финала (финал не дошёл из-за обрыва
   сессии). Теперь блокировка считается по `has_submitted_for_ticket` (тот же критерий,
   что и в 409-проверке `upload_probnik_final`) — цикл без финала больше не блокирует,
   ученик может пересдать без удаления цикла админом. Тест:
   `test_mock_exam_intermediate_only_cycle_does_not_lock_subject` в
   `tests/test_routes_upload.py`. Прогон: `pytest tests/test_routes_upload.py
   tests/test_routes_cycle_upload.py tests/test_exam_cycle.py -q` → 60 passed.

2. **Добавлена диагностика** в `app/dependencies.py::get_current_user` — на каждый из
   трёх вариантов 401 ("Нет сессии", "Сессия не найдена", "Сессия истекла") теперь
   пишется `log.warning(...)` с путём запроса, префиксом session_id (8 символов, не
   полный — это креды), user_id и (для "истекла") `overdue_sec` — на сколько секунд
   просрочена сессия.

## Что осталось — найти и устранить первопричину

Сама причина спонтанного 401 на цепочке `/upload/probnik/intermediate` (200 OK) →
`/upload/probnik/final` (401) ещё не найдена. Гипотезы не подтверждены — нужны
реальные данные из логов.

**Шаги:**
1. Спросить у владельца, повторялась ли ошибка после деплоя диагностики
   (2026-06-14, ~15:38 UTC).
2. Если да — посмотреть логи на сервере:
   ```bash
   ssh <см. memory reference_vps_access.md>
   cd /home/portfolio-saas
   docker compose -f docker-compose.prod-ru.yml logs app | grep "Auth 401"
   ```
3. По найденной записи определить:
   - Какой именно из трёх вариантов сработал ("Нет сессии" / "Сессия не найдена" /
     "Сессия истекла").
   - На каком пути (`/upload/probnik/final`?).
   - Если "истекла" — `overdue_sec`: доли секунды → гонка/race-condition в
     sliding-refresh или cookie; часы → реальный таймаут (session_ttl_hours,
     CSRF `_MAX_AGE` в `app/csrf.py`).
4. Сделать root-cause фикс в owner-слое (`app/dependencies.py` для session-логики,
   `app/csrf.py` для CSRF, `app/templates/upload_mock.html` если проблема в JS-цепочке
   `doUploadIntermediate → doUploadFinal`).
5. После фикса прогнать relevant pytest, задеплоить (`set -a && source .env.deploy &&
   set +a && python scripts/deploy.py`), удалить этот файл `NEXT-SESSION-PROMPT.md`.

## Контекст

- `app/services/exam_cycle.py` — `get_active_ticket`, `has_submitted_for_ticket`,
  `get_or_create_cycle_for_probnik`, `close_cycle`/`reopen_cycle`.
- `app/api/cycle_upload.py` — `upload_probnik_intermediate` (line ~343),
  `upload_probnik_final` (line ~235), оба требуют `require_csrf`.
- `app/dependencies.py::get_current_user` — session validation + sliding refresh
  (extends `expires_at` и обновляет cookie, если до истечения < половины TTL).
- `app/csrf.py` — CSRF-токен = `dumps(session_id)`, `_MAX_AGE = 6h`.
- CLAUDE.md: root-cause first, owner-layer fix, не трогать `.env`/секреты,
  деплоить самостоятельно после фикса, тесты только для изменённого кода,
  отвечать на русском.
