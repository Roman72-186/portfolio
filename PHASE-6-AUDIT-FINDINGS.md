# Фаза 6 — находки и реализация (2026-07-05)

Материал собран по плану `peppy-chasing-micali.md`, Фаза 6. Изначально собирался
как чистый список находок без кода; по решению владельца пункты 1, 2 и 4
впоследствии реализованы в этом же чате (см. «Статус реализации» в каждом
разделе). Пункт 3 закрыт как «проверено, без находок» ещё на этапе сбора.

---

## 1. N+1-запросы в списках учеников куратора/админа

**Локализовано точно** (не общее подозрение, а конкретный блок):
[cabinet_students_shared.py:338-364](app/api/cabinet_students_shared.py#L338-L364),
внутри `students_panel()`.

Остальная часть `students_panel()` уже написана правильно — счётчики/средние баллы
агрегируются `GROUP BY` одним запросом на всех учеников (`counts_by_user`,
`avg_by_user`, `mock_counts_by_user` и т.д., см. комментарий в коде «O(students) not
O(works)»). Проблема только в блоке «сдал/не сдал по активному билету» для куратора
(`role_rank == 2`):

```python
any_ticket_active = any(
    get_active_ticket(db, s["id"], subject) is not None
    for s in sidebar_students
    for subject in MOCK_SUBJECTS
)
if any_ticket_active:
    for s in sidebar_students:
        for subject in MOCK_SUBJECTS:
            ticket = get_active_ticket(db, s["id"], subject)          # снова
            if ticket:
                has_submitted_for_ticket(db, s["id"], subject, ticket.id)  # +1 запрос
```

`get_active_ticket()` → `get_active_tickets()`
([app/services/exam_cycle.py:40-99](app/services/exam_cycle.py#L40-L99)) сама по
себе не одна query, а минимум 2-3 (`is_subject_allowed_for_student`,
`get_matching_target_tag_ids_for_student`, основной join-запрос). Она вызывается
**дважды на каждую пару (ученик × предмет)** — один раз в `any()`, второй раз в
основном цикле — плюс ещё один запрос `has_submitted_for_ticket()` при найденном
билете.

Итог для куратора с N учениками × 2 предмета (`MOCK_SUBJECTS`): до `~2 × N × 2 × 3 =
12N` запросов только на этот блок. При 40 учениках — под 500 запросов на один
рендер страницы `/cabinet/students`.

`services/stats.py` при отдельной проверке (`score_curve_12m`,
`student_score_curve`, `mock_period_subject_stats`, `curator_avg_scores`) — везде
одна агрегирующая query на функцию, N+1 не найден. Подозрение из плана по этому
файлу не подтвердилось.

**Что дальше:** профилировать реальным `EXPLAIN`/`echo=True` на проде с типичным
количеством учеников куратора, затем решить — кэшировать
`get_active_tickets`/`is_subject_allowed_for_student` на время запроса (functools
cache по `(user_id, subject)`) или один раз посчитать активные билеты по всем
ученикам куратора одним batched-запросом.

**Статус реализации:** сделан безопасный, контейнерный фикс — устранён именно
дубль вызова (`any()` + основной цикл считали `get_active_ticket` дважды на
каждую пару). Теперь строится `active_ticket_by_key` один раз, `any()` и цикл
переиспользуют готовый результат — вдвое меньше вызовов резолвера. Глубже (кэш
внутри `get_active_tickets`/`is_subject_allowed_for_student` в
`services/exam_cycle.py`) сознательно не трогал — этот файл активно меняется
параллельным WIP (staged-загрузка, kind-фильтр), рисковать чужими правками не
стал. См. [cabinet_students_shared.py:338-364](app/api/cabinet_students_shared.py#L338-L364).

---

## 2. Race condition в потоке «финал + до 10 этапных» (`cycle_upload.py`)

**Подтверждено: защиты от гонки нет.**

[`upload_probnik_final()`](app/api/cycle_upload.py#L299-L403) делает
check-then-act без блокировки:

1. Строка 321-336: если нет `available_tickets` (т.е. `has_submitted_for_ticket`
   уже True для всех активных билетов) — отбить с 409 «работа сдана».
2. Между этой проверкой и фактической записью проходит `await _read_photos(...)`
   (I/O, строка 351) — окно для гонки.
3. Строка 355 и далее: `get_or_create_cycle_for_probnik` → `_upload_and_save` /
   `_overwrite_final` — создаёт `Work` с `is_final=True`.

Никакой `SELECT ... FOR UPDATE`, `db.begin_nested()` или уникального constraint на
`Work` ([app/models/work.py](app/models/work.py) — проверено, `UniqueConstraint`
на `(cycle_id, is_final)` отсутствует), который бы поймал гонку на уровне БД.

Это тот же класс бага, что уже был найден и исправлен для legacy mock-exam
(`debugging_mock_exam_double_attempt.md` в memory — там потребовался pre-check
через `MockExamLock` ДО создания `Work`, потому что просто `if` на Python-уровне
не спасает от параллельных запросов). Здесь аналогичной блокировки нет.

**Практический риск:** двойной тап «Отправить» на медленном интернете или
дублирующий retry на клиенте может создать два `Work` с `is_final=True` в одном
цикле — `_find_existing_final()` берёт `.first()`, так что затем один из
дублей «теряется» из UI, но обе строки и оба файла в S3 остаются висеть.

**Что дальше:** нужно решение владельца — либо уникальный constraint на уровне БД
(`cycle_id + is_final=True` через partial unique index, т.к. `is_final` не всегда
True) с обработкой `IntegrityError`, либо блокировка по образцу `MockExamLock`
до записи.

**Статус реализации:** добавлен partial unique index
`uq_works_cycle_final_attempt` на `(cycle_id, work_type, attempt_number)` WHERE
`is_final` ([app/models/work.py](app/models/work.py), миграция
`c3d4e5f6a7b8_works_unique_final_per_cycle.py`) + `_upload_and_save()` в
`cycle_upload.py` теперь оборачивает вставку `Work`/`UploadLog` в
`db.begin_nested()` и ловит `IntegrityError`, превращая гонку в штатный
`success=0`/`last_error` вместо 500 или тихого дубликата.

Важное уточнение по scope: constraint изначально делался на `(cycle_id,
work_type)` без `attempt_number`, но это сломало 2 pre-existing теста
(`tests/test_exam_cycle.py::test_attempt_number_increment_for_mock_exam` и
`test_attempt_number_separate_for_retake`) — они намеренно проверяют, что
`next_attempt_number()` считает НЕСКОЛЬКО исторических финалов с разными
`attempt_number` в одном цикле. Сузил constraint до
`(cycle_id, work_type, attempt_number)` — этого достаточно, чтобы поймать
именно гонку (два параллельных запроса вычисляют один и тот же
`next_attempt_number` ДО того, как другой закоммитил), не покушаясь на
существующий (протестированный) инвариант нумерации попыток. Перед миграцией
проверено на проде: дублей по обоим вариантам ключа не было.

---

## 3. `cabinet_students.html` — брейкпоинты 375/768/1024px

**Проверено вручную (Playwright, прод, суперадмин) — критичных багов не найдено.**

- **1024px** — уже полноценный desktop 3-колоночный layout (левое меню + список +
  детали), без наложений и обрезаний. Имена в списке корректно truncate с «…».
- **768px и 375px** — приложение переключается на компактный layout: левое меню
  пропадает, снизу закреплённый pill-nav (`Кабинет/Ученики/Цикл/…`) — это
  общее для всего приложения поведение (то же самое видно и на
  `cabinet_feedback_detail.html`, и на mock-check/retake-check), не специфично для
  этой страницы.
- Первое впечатление по full-page скриншотам — будто закреплённый нижний nav
  перекрывает «Редактировать анкету» и карточки «Портфолио/Пробники» — **это
  оказался артефакт full-page скриншота** (`position: fixed` элемент вставляется в
  каждую позицию склеенного скриншота). Проверено `elementFromPoint()` и обычным
  viewport-скриншотом после реального скролла — на практике ссылка полностью
  доступна и не перекрыта.

**Вывод:** пункт можно закрыть как «проверено, без находок» — специальных
доработок не требуется, если не считать общий (не специфичный для этой страницы)
вопрос дизайна: 768px уже получает мобильный, а не планшетный layout — если это
не осознанное решение, стоит спросить владельца, устраивает ли его такая граница.

---

## 4. Тестовое покрытие `gallery.py` и `cabinet_admin.py`

**Подтверждено: прямых тестов нет.**

- `app/api/cabinet_admin.py` — ни одного файла в `tests/` не покрывает
  `_load_dashboard_data()` ([cabinet_admin.py:38-198](app/api/cabinet_admin.py#L38-L198),
  ~160 строк агрегаций для главного admin/SA дашборда) или сами роуты
  `/admin/mock-check`, `/admin/retake-check` — `grep` по `tests/` не находит
  упоминаний `cabinet_admin`.
- `app/api/gallery.py` — единственные тесты (`tests/test_student_behavior.py:319-361`)
  проверяют собственную галерею ученика (`/cabinet/gallery` 200, группировка по
  месяцам, 404 на чужой файл через `/cabinet/gallery/thumb/{id}`) — это про
  доступ и рендер, не про корректность агрегационных запросов как таковых.

**Что дальше:** нужны тесты, фиксирующие корректность подсчётов в
`_load_dashboard_data` (например: N успешных работ → правильный total; N
непроверенных → правильный `total_unchecked`) — с учётом принципа проекта
«тестировать поведение, не реализацию».

**Статус реализации:** добавлены `tests/test_cabinet_admin_dashboard.py` (3
теста: активные/неактивные пользователи считаются раздельно, works_by_type +
total_works + works_this_month корректны по типу/месяцу/статусу, avg_score
считает только оценённые работы, unscored_mocks корректно равен 0 без
активного периода мок-экзамена — фиксирует именно ветку `else: unscored_mocks
= 0`) и `tests/test_gallery_aggregations.py` (3 теста: счётчики по типам работ
через группировку по месяцам, исключение status≠success и чужих Work,
раздельный подсчёт total_success/total_failed/total_photos в `/cabinet/history`).

---

## Дополнительно: найдено в ходе визуальной проверки Фазы 4 (не Фаза 6, но тоже кандидат)

На мобильной ширине (375px) programmatic `scroll_into_view_if_needed()` к нижней
кнопке панели скоринга на `/cabinet/admin/mock-check` может на момент скролла
поставить кнопку под закреплённый нижний nav-pill. При обычном скролле колесом
кнопки корректно стекаются в столбец над нав-баром без перекрытия (см. переписку
сессии). Не связано с изменением теней/hover в Фазе 4 (чистый CSS `box-shadow` +
`translateY` не может сдвинуть layout) — существовавшее раньше поведение
закреплённого нижнего nav. Кандидат в общий пункт «нижний pill-nav и последний
элемент прокручиваемой панели» для будущего мобильного прохода.
