# Portfolio SaaS: audit/refactor handoff

Этот файл - единая рабочая память для аудита и поэтапного рефакторинга проекта `Roman72-186/portfolio-saas`.

Любой агент, который продолжает работу, сначала читает этот файл, затем обновляет его после каждого значимого шага. Не создавать параллельные `task_plan.md`, `findings.md`, `progress.md`, если пользователь отдельно не попросит. Все статусы, находки, решения, измененные файлы и проверки фиксировать здесь.

## Текущий статус

- Статус: phase_3_in_progress
- Последнее обновление: 2026-06-07
- Текущая фаза: 3. Выполнение refactor slices
- Следующий шаг: P2-02 staff/student access helper; это security-sensitive slice, начинать только с service/route contract tests и чтения affected access checks.
- Важное уточнение пользователя: burger menu у каждой роли свое по составу и смыслу. Нельзя делать одинаковое меню для всех ролей. Допустимо централизовать механизм/конфиг/рендеринг, только если сохраняется разный набор пунктов и текущее поведение каждой роли.

## Контекст проекта

- Стек: FastAPI, Jinja2, SQLAlchemy, Alembic, PostgreSQL.
- Есть тесты в `tests/`.
- Основной код: `app/`.
- Роутеры: `app/api/`.
- Шаблоны: `app/templates/`.
- Сервисы: `app/services/`.
- Модели: `app/models/`.
- Миграции: `alembic/`.
- Статика: `app/static/`.
- Локальные команды из `CLAUDE.md`:
  - `pytest`
  - `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
  - `alembic upgrade head`
  - `alembic revision --autogenerate -m "msg"`

## Жесткие правила

- Не переписывать проект целиком.
- Не удалять бизнес-логику без доказанного дубля и проверки.
- Не менять смысл ролей.
- Не ломать авторизацию, impersonation, n8n webhook, загрузку работ, обратную связь, кабинеты пользователей.
- Не менять production-настройки и не деплоить без отдельной команды.
- Не выводить секреты из `.env`.
- Перед изменениями всегда делать аудит связанного потока сверху вниз и снизу вверх.
- Если меняется роль/право/меню, проверять связанные слои: dependency, service, route, template, tests.
- Из-за текущего dirty worktree не откатывать чужие изменения. Работать только с явно нужными файлами.

## Протокол обновления этого файла

После каждого этапа обновлять:

1. `Текущий статус`.
2. Чеклист соответствующей фазы.
3. `Находки аудита`.
4. `Решения`.
5. `Измененные файлы`.
6. `Проверки`.
7. `Следующий агент: что делать дальше`.

Формат статусов:

- `pending` - не начато.
- `in_progress` - в работе.
- `blocked` - есть блокер, указать причину.
- `done` - завершено и проверено настолько, насколько возможно.

## План по этапам

### Фаза 0. Подготовка

Статус: done

- [x] Прочитать внешнюю инструкцию `codex_portfolio_saas_audit_refactor_instruction.md`.
- [x] Учесть уточнение: burger menu у каждой роли свое.
- [x] Проверить, что репозиторий `portfolio-saas` существует.
- [x] Зафиксировать единый файл хенд оффа.
- [x] Перед кодовыми изменениями дополнительно прочитать родительский `../CLAUDE.md`.

Результат: создан этот файл.

### Фаза 1. Полный аудит без изменений

Статус: done

Цель: получить карту текущей архитектуры и доказательно найти дубли.

Сделать:

- [x] Найти все FastAPI routers и все `include_router`.
- [x] Составить таблицу endpoint -> router -> template -> dependency -> роль/permission.
- [x] Найти role-specific роуты, похожие по смыслу: student/curator/admin/superadmin.
- [x] Найти все Jinja templates и какие роуты их используют.
- [x] Найти base/layout templates.
- [x] Найти navigation/sidebar/bottom_nav/burger/menu templates.
- [x] Зафиксировать, чем реально отличается меню каждой роли.
- [x] Найти все проверки ролей в Python.
- [x] Найти все проверки ролей в Jinja.
- [x] Найти текущий RBAC-слой и его реальное использование.
- [x] Найти повторяющиеся карточки, таблицы, кнопки, формы, бейджи.
- [x] Найти n8n webhook endpoints и связанные services/tests.
- [x] Найти upload/storage/S3/Google Drive логику.
- [x] Найти бизнес-логику, которая живет прямо в routers.
- [x] Найти существующие тесты, которые покрывают затрагиваемые зоны.

Ожидаемый результат фазы:

- Заполнить разделы `Находки аудита`, `Карта роутеров`, `Карта шаблонов`, `Карта RBAC`, `Карта меню`, `Карта webhook/storage`.
- Не менять код.

### Фаза 2. Архитектурный план refactor slices

Статус: done

Цель: разбить большой рефакторинг на маленькие безопасные изменения.

Сделать:

- [x] Определить, какие дубли можно убрать сразу, а какие оставить до следующего этапа.
- [x] Выбрать минимальный owner layer для RBAC: например, `app/services/rbac.py` или новый `app/core/*`, если это не конфликтует с текущей структурой.
- [x] Спроектировать menu config с сохранением отдельных меню по ролям.
- [x] Решить, что должно быть единым: helper/renderer меню, CSS/JS поведения burger, возможно partial.
- [x] Решить, что должно остаться role-specific: набор пунктов, порядок, названия, доступные действия.
- [x] Выбрать первый небольшой slice для реализации.
- [x] Зафиксировать риски и проверки для каждого slice.

Ожидаемый результат фазы:

- Список этапов реализации с приоритетом.
- Понятная граница первого PR/изменения.

### Phase 2 result: refactor slices

Главная стратегия: сначала зафиксировать контракты текущего поведения тестами, затем выносить только маленькие owner-layer куски. Не начинать с массового объединения страниц, ролей, меню, storage или webhook flows.

#### P1: самые безопасные и полезные

##### P1-01. Contract tests для role dispatcher и role-specific menu

- Цель: зафиксировать текущее поведение входа, `/cabinet` redirect и видимость меню до любых refactor changes.
- Файлы-кандидаты: `tests/test_routes_cabinet.py`, `tests/test_routes_login.py`, возможно новый `tests/test_navigation_contracts.py`; читать `app/api/cabinet.py`, `app/templates/partials/bottom_nav.html`, `app/templates/partials/staff_nav.html`, `app/templates/_curator_nav.html`, `app/templates/base.html`.
- Что можно вынести/упростить: пока ничего в приложении; только добавить/уточнить тестовые fixtures/assertions для текущих пунктов меню и redirects.
- Что нельзя менять: `ROLE_CABINET_MAP`, названия ролей, rank semantics, состав меню, include-логику `base.html`, URLs пунктов меню.
- Риск: низкий; возможен риск нестабильных HTML-assertions, поэтому проверять по устойчивым href/text фрагментам.
- Порядок выполнения: сначала зафиксировать student/admin/superadmin `/cabinet` behavior, затем curator pages/menu, затем явно описать текущее отсутствие отдельного moderator menu как known contract.
- Тесты: `pytest tests/test_routes_cabinet.py tests/test_routes_login.py`; если создан отдельный файл, добавить `pytest tests/test_navigation_contracts.py`.
- Критерий готовности: тесты проходят, в них явно видно, что student, curator, admin/superadmin меню не являются одним одинаковым списком.

##### P1-02. Малые Jinja partials/macros для UI atoms без форм и прав

- Цель: убрать самые простые UI-дубли без изменения данных, форм, CSRF, routes и role checks.
- Файлы-кандидаты: `app/templates/cabinet_curator_reports.html`, `app/templates/cabinet_feedback_detail.html`, `app/templates/cabinet_staff_cycles.html`, `app/templates/superadmin_*`, новый или существующий partial в `app/templates/partials/`.
- Что можно вынести/упростить: бейдж статуса, маленькую карточку пустого состояния, повторяющиеся классы кнопок/меток, если входные данные простые и не содержат permission branching.
- Что нельзя менять: `form action`, `method`, `name`, `id`, CSRF input, тексты ошибок, URLs, условия `user.role_rank`, `viewer_role`, `is_superadmin`.
- Риск: низкий/средний; визуальная регрессия или случайное скрытие кнопки.
- Порядок выполнения: выбрать один атом на двух страницах, вынести partial/macro, проверить render tests; не объединять крупные pages.
- Тесты: `pytest tests/test_feedback_dialog.py tests/test_curator_reports_stats.py tests/test_routes_cabinet_superadmin.py` по затронутым страницам.
- Критерий готовности: HTML содержит прежние href/form fields/text, страницы render без 500, визуальная структура ролей не меняется.

##### P1-03. Pure upload validation helper без S3/n8n/Drive контрактов

- Цель: уменьшить дубли проверок размера/типа файлов, не меняя загрузку, storage path, `Work`, `UploadLog`, `drive_status` и фоновые webhooks.
- Файлы-кандидаты: новый `app/services/upload_validation.py` или близкий existing service; точечно `app/api/upload.py`, `app/api/cycle_upload.py`, `app/api/cabinet_superadmin.py`, `app/api/cabinet_students_shared.py`; tests around upload.
- Что можно вынести/упростить: чистые функции/константы для image MIME/extension/size/count validation и единых сообщений, если текущие сообщения совпадают.
- Что нельзя менять: S3 path builders, `send_photo_to_n8n`, Drive sync, background tasks, `drive_status`, idempotency keys, response JSON shape, HTTP status codes.
- Риск: средний; upload flows похожи, но контракты разные.
- Порядок выполнения: сначала тесты текущих accepted/rejected file types; затем один helper для одного потока; затем подключать второй поток только если сообщения/status полностью совпадают.
- Тесты: `pytest tests/test_routes_upload.py tests/test_routes_cycle_upload.py tests/test_routes_cabinet_superadmin.py tests/test_routes_cabinet_curator_new.py`.
- Критерий готовности: все upload tests проходят, legacy upload продолжает вызывать n8n/Drive, cycle upload остается `s3_only`.

#### P2: средний риск, делать после P1 contracts

##### P2-01. Role menu config/helper с разными меню по ролям

- Цель: сделать понятный источник данных для меню, сохранив разные наборы пунктов для student, curator, admin и superadmin.
- Файлы-кандидаты: новый `app/services/navigation.py` или `app/menu.py`, `app/templates/partials/bottom_nav.html`, `app/templates/partials/staff_nav.html`, `app/templates/_curator_nav.html`, `app/templates/base.html`, route contexts that pass `active_tab`.
- Что можно вынести/упростить: данные пунктов меню, активное состояние, общий renderer только если он принимает уже готовый role-specific список.
- Что нельзя менять: состав пунктов, порядок, URLs, условия rank visibility, отдельный curator top nav, отсутствие отдельного moderator menu без product decision.
- Риск: средний; легко случайно сделать одинаковое меню или показать staff item ученику.
- Порядок выполнения: после P1-01; сначала config для одной роли без смены HTML, потом renderer behavior, затем остальные роли.
- Тесты: `pytest tests/test_navigation_contracts.py tests/test_routes_cabinet.py tests/test_routes_login.py`.
- Критерий готовности: contract tests показывают прежний состав меню для каждой роли.

##### P2-02. Staff/student access helper для ownership checks

- Цель: вынести повторяющуюся проверку доступа к ученикам в owner service, не меняя IDOR semantics.
- Файлы-кандидаты: `app/api/cabinet_students_shared.py`, `app/api/cabinet_curator.py`, `app/api/feedback.py`, возможно новый `app/services/access.py`.
- Что можно вынести/упростить: проверки "куратор видит только своих", "admin/superadmin видят всех", helper для rank/user_id/student.curator_id.
- Что нельзя менять: какие роли получают доступ, redirect/status code при отказе, тексты ошибок, feedback closed-cycle rules.
- Риск: средний/высокий; это security-sensitive слой.
- Порядок выполнения: сначала добавить service tests на текущие правила; заменить один route; затем расширять.
- Тесты: `pytest tests/test_routes_cabinet_curator_new.py tests/test_feedback_dialog.py tests/test_routes_cabinet_superadmin.py`.
- Критерий готовности: запрещенные сценарии curator чужой student остаются запрещенными, admin/superadmin сценарии не ломаются.

##### P2-03. User management service alignment

- Цель: уменьшить дубли rank checks в user management, используя уже существующий `app/services/user_management.py`.
- Файлы-кандидаты: `app/services/user_management.py`, `app/api/cabinet_superadmin.py`, `app/api/admin.py`, `tests/test_services_user_management.py`, `tests/test_routes_cabinet_superadmin.py`, `tests/test_routes_admin.py`.
- Что можно вынести/упростить: block/delete/toggle/manage checks, если они совпадают с `_can_manage_user`.
- Что нельзя менять: superadmin-only mutations, impersonation, session invalidation, target rank protection, response redirects.
- Риск: средний/высокий; затрагивает админские мутации.
- Порядок выполнения: сначала service tests на все rank pairs, затем один endpoint, затем остальные.
- Тесты: `pytest tests/test_services_user_management.py tests/test_routes_cabinet_superadmin.py tests/test_routes_admin.py tests/test_routes_superadmin_impersonate.py`.
- Критерий готовности: нельзя управлять равным/старшим rank, после мутаций User сохраняется cache invalidation behavior.

##### P2-04. Upload save pipeline helper для одного потока

- Цель: уменьшить router business logic после P1-03, но только внутри одного потока за раз.
- Файлы-кандидаты: `app/api/upload.py` или `app/api/cycle_upload.py`, новый service рядом с validation helper, `app/services/s3.py`.
- Что можно вынести/упростить: compress -> S3 upload -> Work/UploadLog creation для одного явно выбранного flow.
- Что нельзя менять: n8n payload, background retry, `drive_status`, S3 path, JSON response shape, transaction boundaries.
- Риск: средний/высокий; storage и async side effects.
- Порядок выполнения: начинать с cycle upload как `s3_only`, потому что он не должен запускать Drive/n8n; legacy upload переносить позже.
- Тесты: `pytest tests/test_routes_cycle_upload.py tests/test_exam_cycle.py tests/test_routes_upload.py`.
- Критерий готовности: cycle upload создает прежние Work records с `drive_status="s3_only"` и не дергает n8n.

#### P3: отложить до отдельного анализа

##### P3-01. RBAC/permissions migration

- Цель: централизовать права, но только после отдельного миграционного плана.
- Файлы-кандидаты: `app/services/rbac.py`, `app/dependencies.py`, `app/api/cabinet.py`, role-specific routers, migrations/seeds.
- Почему отложить: любое изменение ролей/permissions влияет на seed, dispatcher, templates, session cache и tests.
- Нельзя менять без плана: названия ролей, ranks, `ROLE_PERMISSIONS`, `ROLE_CABINET_MAP`, aliases `require_*`, user cache invalidation.

##### P3-02. Storage/n8n/Drive contract refactor

- Цель: привести upload/storage/webhook слой к единому контракту, но только после отдельной контрактной карты.
- Файлы-кандидаты: `app/api/upload.py`, `app/api/cycle_upload.py`, `app/api/cabinet_students_shared.py`, `app/api/cabinet_superadmin.py`, `app/services/n8n.py`, `app/services/drive.py`, `app/services/s3.py`.
- Почему отложить: legacy upload, cycle upload, staff upload, feedback photo, curator video и Drive retry имеют разные side effects.
- Нельзя менять без плана: JSON payload в n8n, `X-Webhook-Secret`, retry/idempotency, `drive_status`, Drive cache invalidation, S3 key format.

##### P3-03. Массовое объединение шаблонов/страниц

- Цель: возможное сокращение шаблонов, но только после проверки active/legacy usage.
- Файлы-кандидаты: legacy templates без прямого `TemplateResponse`, role-specific dashboards, large admin/curator/student templates.
- Почему отложить: похожие страницы скрывают разные роли, actions, forms, redirects и business rules.
- Нельзя менять без плана: крупные pages, CSS class contracts, form names, active tabs, role-specific blocks.

##### P3-04. Moderator menu/role decision

- Цель: определить продуктовый смысл роли `модератор`.
- Файлы-кандидаты: `app/api/cabinet.py`, `app/dependencies.py`, `app/services/rbac.py`, templates/menu.
- Почему отложить: сейчас dispatcher ведет moderator на student path, а `_require_student_panel` rank 3 не пускает; это не refactor-only задача.
- Нельзя менять без плана: redirect moderator, menu visibility, permissions.

### План проверки

После каждого slice сначала запускать узкие тесты по затронутому контракту, затем расширенный набор для соседних потоков. Полный `pytest` запускать после завершения группы slices или перед передачей результата.

- P1-01 menu/contracts: `pytest tests/test_routes_cabinet.py tests/test_routes_login.py`, плюс новый `pytest tests/test_navigation_contracts.py`, если файл добавлен.
- P1-02 UI partials: `pytest tests/test_feedback_dialog.py tests/test_curator_reports_stats.py tests/test_routes_cabinet_superadmin.py` по затронутым pages.
- P1-03 upload validation: `pytest tests/test_routes_upload.py tests/test_routes_cycle_upload.py tests/test_routes_cabinet_superadmin.py tests/test_routes_cabinet_curator_new.py`.
- P2-01 menu config/helper: `pytest tests/test_navigation_contracts.py tests/test_routes_cabinet.py tests/test_routes_login.py`.
- P2-02 ownership helper: `pytest tests/test_routes_cabinet_curator_new.py tests/test_feedback_dialog.py tests/test_routes_cabinet_superadmin.py`.
- P2-03 user management: `pytest tests/test_services_user_management.py tests/test_routes_cabinet_superadmin.py tests/test_routes_admin.py tests/test_routes_superadmin_impersonate.py`.
- P2-04 upload save pipeline: `pytest tests/test_routes_cycle_upload.py tests/test_exam_cycle.py tests/test_routes_upload.py`.
- После любого Python refactor: `python -m compileall app tests`.
- Перед финальной передачей после code slices: `pytest`.

Ручные сценарии по ролям:

- Student: login -> `/cabinet`, dashboard, portfolio, cycle, mock upload, feedback dialog, bottom nav на mobile/desktop.
- Curator: login -> `/cabinet/curator` redirect, students list, reports, statistics tab, own/foreign student access, curator menu remains curator-specific.
- Admin: login -> dashboard, students, staff cycles, reports, user actions allowed for lower ranks only, staff nav unchanged.
- Superadmin: dashboard, users, impersonation, periods/tickets, Drive sync retry/status, superadmin-only actions remain gated.
- Moderator: не менять без отдельного решения; если tests фиксируют текущее поведение, они должны описывать его как current contract.

Storage/upload scenarios that must not change:

- Legacy portfolio/mock/retake upload still writes S3 and schedules n8n/Drive background sync.
- Cycle upload still writes S3 only and keeps `drive_status="s3_only"`.
- Staff upload for student keeps admin+ access and current ownership checks.
- Feedback photo upload keeps text/photo validation and cycle/permission checks.
- Curator report upload remains video-only S3 flow.
- Ticket image upload keeps admin+ CSRF and image validation.
- Drive retry keeps `pending/synced/failed/s3_only` semantics and does not expose secrets.

### Фаза 3. RBAC и permissions

Статус: in_progress

Цель: централизовать проверки прав без изменения смысла ролей.

Сделать:

- [ ] Сначала описать текущие роли и права как есть.
- [ ] Не переименовывать роли без необходимости.
- [ ] Добавить/улучшить единые helpers: `has_permission`, `require_permission`, `get_user_permissions`.
- [ ] Подключать helpers постепенно, начиная с мест с явным дублем.
- [ ] Не ломать существующие dependencies и session cache.
- [ ] После любых изменений User учитывать правило из `CLAUDE.md`: `app.cache.invalidate_session(session_id)`.
- [ ] Добавить/обновить тесты для разрешенных и запрещенных доступов.

Ожидаемый результат фазы:

- Меньше ручных проверок ролей.
- Текущие права ролей сохранены.

Выполнено:

- [x] P1-01: добавлены contract tests для текущего `/cabinet` role dispatcher и role-specific меню без изменения приложения.
- [x] Зафиксированы текущие redirects: `ученик -> /cabinet/student`, `куратор -> /cabinet/curator`, `модератор -> /cabinet/student`, `админ -> /cabinet/admin-panel`, `суперадмин -> /cabinet/superadmin`.
- [x] Зафиксировано, что student использует `bottom_nav`, curator reports используют `_curator_nav`, admin/superadmin используют `staff_nav`.
- [x] Зафиксировано текущее поведение moderator: redirect на student dashboard, но нет доступа к `/cabinet/students`.
- [x] P1-02: вынесен маленький Jinja macro `exam_assignment_status_badge` для статусов exam assignments.
- [x] P1-02 сохранил прежний HTML-контракт `status-badge status-*`, русские labels и локальные CSS-классы; формы, CSRF, routes, RBAC и меню не менялись.
- [x] P1-03: вынесен pure helper `app/services/upload_validation.py` для image MIME/extension/size/count validation.
- [x] P1-03 подключен к `app/api/upload.py` и `app/api/cycle_upload.py` через локальные wrappers, чтобы сохранить прежние HTML/JSON ошибки, HTTP status codes и дальнейшие S3/n8n/Drive side effects.
- [x] P1-03 не менял S3 path builders, `send_photo_to_n8n`, Drive sync, `drive_status`, `Work`, `UploadLog`, JSON response shape и role-specific меню.
- [x] P2-01a: создан `app/services/navigation.py` и вынесен source of truth для `_curator_nav.html` через `curator_nav_items()`.
- [x] P2-01a сохранил текущий curator menu contract: `Ученики`, `Отчёты`, `Статистика` с теми же URLs и active keys; `bottom_nav`, `staff_nav`, `base.html`, RBAC, routes и состав меню других ролей не менялись.
- [x] P2-01b: вынесен source of truth для student `bottom_nav` через `student_nav_items()`.
- [x] P2-01b сохранил текущий student menu contract: desktop `/cabinet`, mobile `/cabinet/student`, `Портфолио`, `Цикл/Цикл Пробника`, `Пробник`, `3D Лаб`, прежние active keys и aria labels.
- [x] P2-01c: вынесен source of truth для admin/superadmin staff `staff_nav` через `staff_nav_items(role_rank)`.
- [x] P2-01c сохранил текущий staff menu contract: admin/superadmin видят `Кабинет`, `Ученики`, `Цикл Пробника`, `3D Лаб`, `Видео-отчёты`; `mock-check` остается только для `role_rank < 4` при прямом/manual include; desktop `staff-aside`, mobile `staff-pill-nav`, CSS/JS/classes, URLs и active/aria-current expressions сохранены.
- [x] P2-01 закрыт: curator, student и staff menus имеют отдельные role-specific configs/helpers; не создан один одинаковый список для всех ролей.

### Фаза 4. Role-specific burger/menu без дублей механики

Статус: pending

Цель: сохранить свое меню для каждой роли, но убрать хаос в источниках данных и поведении.

Сделать:

- [ ] Зафиксировать текущее меню каждой роли: student, curator, admin, superadmin.
- [ ] Не объединять меню в одинаковый список.
- [ ] Создать/улучшить role menu config, где у каждой роли свой набор пунктов.
- [ ] Сделать единый helper получения меню для текущего пользователя.
- [ ] Если безопасно, использовать общий partial/renderer, который принимает уже готовые пункты.
- [ ] Если визуальная структура у ролей принципиально отличается, не ломать ее в этой фазе; сначала централизовать данные.
- [ ] Проверить mobile/tablet/desktop поведение.
- [ ] Проверить отсутствие горизонтального скролла и закрытие drawer, если drawer есть.

Ожидаемый результат фазы:

- Меню каждой роли остается своим.
- Источник пунктов меню становится понятнее и масштабируемее.

### Фаза 5. Jinja partials/macros/layouts

Статус: pending

Цель: убрать очевидные UI-дубли без смены дизайна.

Сделать:

- [ ] Начать с маленьких повторяющихся элементов: badges, action buttons, empty states.
- [ ] Не объединять крупные страницы, пока не понятны различия данных и прав.
- [ ] Не удалять CSS-классы, которые могут использоваться.
- [ ] Проверять страницы после каждого вынесения partial/macro.
- [ ] Сохранять текущие тексты, URLs, form names, input names, CSRF.

Ожидаемый результат фазы:

- Появляются reusable partials/macros.
- Поведение форм и кнопок не меняется.

### Фаза 6. Routers -> services

Статус: pending

Цель: убрать бизнес-логику из роутеров там, где это явно безопасно.

Сделать:

- [ ] Начать с функций, которые уже повторяются в нескольких routers.
- [ ] Не переносить все подряд.
- [ ] Сначала добавить service-функцию, затем заменить вызовы.
- [ ] Сохранять request/response contracts.
- [ ] Сохранять текущие status codes, redirects, flash/session behavior.
- [ ] Добавить или обновить route/service tests.

Ожидаемый результат фазы:

- Роутеры становятся тоньше.
- Бизнес-логика остается покрыта тестами.

### Фаза 7. n8n webhook

Статус: pending

Цель: проверить и укрепить webhook-слой без изменения внешнего контракта.

Сделать:

- [ ] Найти все webhook endpoints.
- [ ] Проверить payload validation.
- [ ] Проверить защиту endpoint.
- [ ] Проверить логирование ошибок без секретов.
- [ ] Проверить тесты `tests/test_services_n8n.py` и связанные route tests.
- [ ] Не менять JSON contract без явной причины.

Ожидаемый результат фазы:

- Webhook logic понятна и отделена от UI-логики настолько, насколько безопасно.

### Фаза 8. Upload/storage/S3/Google Drive

Статус: pending

Цель: найти и уменьшить дубли upload/storage logic.

Сделать:

- [ ] Найти все upload endpoints.
- [ ] Найти проверки типа/размера файлов.
- [ ] Найти S3 usage.
- [ ] Найти Google Drive usage.
- [ ] Найти повторяющиеся conversion/save/upload блоки.
- [ ] Вынести только очевидные повторы в service.
- [ ] Проверить `tests/test_routes_upload.py` и связанные тесты.

Ожидаемый результат фазы:

- Storage flow понятнее, но внешний upload behavior сохранен.

### Фаза 9. Проверка и ручной QA

Статус: pending

Минимальные команды:

- [ ] `python -m compileall .`
- [ ] `pytest`
- [ ] `alembic current`
- [ ] `alembic history`
- [ ] `uvicorn app.main:app --reload`

Ручная проверка:

- [ ] Вход ученика.
- [ ] Вход куратора.
- [ ] Вход админа.
- [ ] Вход суперадмина.
- [ ] Dashboard каждой роли.
- [ ] Burger/menu каждой роли: состав пунктов именно свой.
- [ ] Mobile menu.
- [ ] Tablet menu.
- [ ] Desktop menu.
- [ ] Загрузка работы.
- [ ] Обратная связь.
- [ ] n8n webhook.
- [ ] Закрытые страницы и отказ доступа.

### Фаза 10. Финальный отчет

Статус: pending

В финале заполнить:

- [ ] Что найдено при аудите.
- [ ] Какие routers дублировались.
- [ ] Какие templates дублировались.
- [ ] Какие partials/macros созданы.
- [ ] Как устроена RBAC-система.
- [ ] Как строится меню с учетом role-specific burger menu.
- [ ] Какие страницы объединены.
- [ ] Какие services вынесены.
- [ ] Какие файлы изменены.
- [ ] Какие команды проверки запущены.
- [ ] Какие места требуют ручной проверки.
- [ ] Что улучшить следующим этапом.

## Находки аудита

Фаза 1 выполнена без изменений кода. Аудит шёл сверху вниз (`app.main` -> `include_router` -> endpoints/templates/dependencies) и снизу вверх (`dependencies/rbac/services` -> routes/templates/tests).

### Предварительные наблюдения без глубокого аудита

- В `app/api/` уже видны крупные role-specific routers: `cabinet_student.py`, `cabinet_curator.py`, `cabinet_admin.py`, `cabinet_superadmin.py`, `cabinet_students_shared.py`.
- В `app/templates/` уже видны role-specific templates: `cabinet_student.html`, `cabinet_curator*.html`, `cabinet_admin*.html`, `cabinet_superadmin.html`, `cabinet_staff.html`, `cabinet_students.html`.
- В `app/templates/partials/` есть `bottom_nav.html` и `staff_nav.html`; также есть `app/templates/_curator_nav.html`.
- Уже есть `app/services/rbac.py`, `app/services/n8n.py`, `app/services/s3.py`, `app/services/drive.py`, значит перед созданием новых core-файлов нужно проверить существующие owner layers.
- В репозитории есть много незакоммиченных изменений до начала этого плана. Не считать их изменениями текущего агента.

### Фаза 1: конкретные выводы

- `app/main.py` подключает 12 router modules: `auth`, `cabinet`, `cabinet_student`, `cabinet_curator`, `cabinet_admin`, `cabinet_superadmin`, `cabinet_students_shared`, `upload`, `cycle_upload`, `feedback`, `gallery`, `admin`.
- Всего найдено 138 route decorators в `app/api/*.py`. Самые большие owner zones: `cabinet_superadmin.py` (38 endpoints), `cabinet_students_shared.py` (17), `auth.py` (14), `cabinet_curator.py` (13), `upload.py` (11), `admin.py` (11), `feedback.py` (10).
- Runtime RBAC split: `app/services/rbac.py` хранит seed roles/permissions, `app/dependencies.py` формирует `user` dict и aliases `require_student/curator/admin_role/superadmin`, а бизнес-правила ownership/role exceptions частично живут прямо в routers.
- `require_permission()` существует, но реальное использование почти отсутствует; в feedback используется ручная проверка строки `"feedback.write" in user["permissions"]`.
- Есть role dispatcher `app/api/cabinet.py::ROLE_CABINET_MAP`: `суперадмин -> /cabinet/superadmin`, `админ -> /cabinet/admin-panel`, `модератор -> /cabinet/student`, `куратор -> /cabinet/curator`, `ученик -> /cabinet/student`.
- Role-specific меню действительно разное: student использует `partials/bottom_nav.html`; admin/superadmin получают `partials/staff_nav.html` из `base.html`; curator не получает staff-nav из `base.html`, но на отдельных страницах получает `_curator_nav.html`; в некоторых staff/student mixed pages шаблон сам решает, какой nav include показать.
- Не надо объединять меню в один одинаковый список. Безопасный будущий слой - конфиг пунктов по ролям + общий renderer/behavior, если сохраняются разные наборы пунктов.
- Очевидные UI-дубли: sidebar/list/search в `cabinet_students.html`, `cabinet_curator_portfolio.html`, `cabinet_curator_mockexams.html`, `cabinet_curator_retakes.html`, `cabinet_admin_mock_check.html`, `cabinet_admin_retake_check.html`; lightbox уже вынесен в `partials/lightbox.html`; cycle calendar уже частично вынесен в `partials/cycle_day_calendar.html` и `partials/cycle_calendar_lib.html`.
- Часть старых шаблонов не имеет прямого `TemplateResponse` в текущих routers: `cabinet.html`, `cabinet_admin.html`, `cabinet_admin_students.html`, `cabinet_admin_student_works.html`, `cabinet_curator.html`, `cabinet_curator_dashboard.html`, `cabinet_curator_mockexams.html`, `cabinet_curator_portfolio.html`, `cabinet_curator_retakes.html`, `cabinet_superadmin.html`, `scores.html`. Это кандидаты на дополнительную проверку ссылок/истории, не на удаление без отдельного шага.
- Upload/storage слой имеет несколько похожих, но не идентичных реализаций: legacy student upload (`app/api/upload.py`) пишет S3 и затем n8n/Drive в background; cycle upload (`app/api/cycle_upload.py`) пишет только S3 с `drive_status="s3_only"`; staff upload за ученика (`cabinet_students_shared.py`) пишет S3; feedback photo (`services/feedback.py`) пишет S3; curator reports (`cabinet_curator.py`) пишет video в S3; ticket image upload (`cabinet_superadmin.py`) пишет S3.
- Основная бизнес-логика в routers: superadmin dashboard/users/exam assignments, student panel aggregation, upload validation/process, cycle upload, feedback dialog authorization, curator report upload. Это не обязательно ошибка, но первые refactor slices лучше брать только там, где уже есть явный повтор и тесты.
- n8n outbound webhook source of truth - `app/services/n8n.py::send_photo_to_n8n`; webhook secret идёт в header `X-Webhook-Secret`; tests есть в `tests/test_services_n8n.py` и route tests вокруг upload.
- Google Drive read/sync идёт через n8n workflow в `app/services/drive.py`, вызывается после auth login/link и для legacy thumbnails/gallery; прямых Google credentials в приложении нет.
- Покрывающие тесты уже есть: auth/login/admin/cabinet/student/upload/cycle_upload/feedback/superadmin_impersonate/curator_reports/stats/n8n/cache/user_management/performance.

## Карта роутеров

| Router | Prefix/routes | Template/API | Роли | Permissions/dependencies | Комментарий |
|---|---|---|---|---|---|
| `app/api/auth.py` | `/`, `/auth/vk/*`, `/auth/link`, `/login`, `/logout`, `/3dlab`, internal issue-link/SSO | `login.html`, `staff_login.html`, `denied.html`, `blocked.html`, `3dlab.html`, redirects/JSON | public, student, staff, internal | `get_db`, `get_current_user`, `require_internal_api_token`, `require_lab3d_token` | Auth, VK OAuth, one-time links, staff login, 3D Lab SSO. После login/link вызывает Drive sync background. |
| `app/api/cabinet.py` | `/cabinet` | redirect | all authenticated | `get_current_user` | Dispatcher по `ROLE_CABINET_MAP`. Модератор сейчас ведёт на student cabinet. |
| `app/api/cabinet_student.py` | `/cabinet/student`, `/profile`, `/notifications`, `/cycle`, `/portfolio`, `/api/exam-ticket` | `cabinet_student.html`, `profile.html`, `cabinet_notifications.html`, `cabinet_cycle.html`, `cabinet_portfolio.html`, `cabinet_cycle_calendar.html`, JSON/redirects | student rank >= 1 через dependency, фактически student UI | `require_student`, `require_csrf`, `get_db` | Student dashboard/profile/portfolio/cycle. Есть shared render `render_cycle_calendar`, используемый staff feedback routes. |
| `app/api/upload.py` | `/upload`, `/upload/api`, `/upload/mock-exam`, `/upload/mock-exam/api`, `/upload/retake`, `/upload/retake/api`, `/upload/finish-before` | `upload.html`, `upload_mock.html`, `upload_retake.html`, JSON | student | `require_student`, `require_csrf`, `get_db` | Legacy upload: validate -> compress -> S3 -> Work/UploadLog -> background n8n/Drive. Много upload business logic в router. |
| `app/api/cycle_upload.py` | `/upload/probnik/final`, `/upload/probnik/intermediate`, `/upload/otrabotka/final`, `/upload/otrabotka/intermediate` | JSON | student | `require_student`, `require_csrf`, `get_db` | Новый cycle upload: S3 only, `drive_status="s3_only"`, без n8n/Drive. Похож на upload.py, но контракт другой. |
| `app/api/gallery.py` | `/cabinet/gallery`, `/cabinet/gallery/thumb/{file_id}`, `/cabinet/history` | `gallery.html`, `history.html`, thumbnail redirect/404 | student | `require_student`, `get_db` | Legacy gallery/history. Drive thumbnails подтягиваются через `app/services/drive.py`. |
| `app/api/cabinet_curator.py` | `/cabinet/curator`, `/curator/reports`, `/curator/portfolio*`, `/curator/mock-exams*`, `/students/{id}`, `/curator/retakes*`, `/mock-exam/unlock`, `/works/{id}/score` | current: `cabinet_curator_reports.html`, JSON/redirects; часть legacy templates не вызывается напрямую | curator, admin for report delete/score | `require_curator`, `require_admin_role`, `require_csrf`, `get_current_user`, `get_db` | Curator dashboard redirects to `/cabinet/students`; reports video upload в S3. Есть manual ownership checks. |
| `app/api/cabinet_students_shared.py` | `/cabinet/students`, `/students/{id}/profile|portfolio|mock-exams|statistics|retakes`, staff actions/upload/delete/move | `cabinet_students.html`, JSON | curator owns students, admin/superadmin all students | `_require_student_panel`, `require_admin_role`, `require_csrf`, `get_db` | Unified student card for staff. Собственный access helper: curator rank 2 or rank >=4, moderator rank 3 denied. Много aggregation/upload logic в router. |
| `app/api/feedback.py` | `/cabinet/feedback/*`, `/cabinet/staff/cycles`, `/cabinet/students/{id}/cycles`, `/cabinet/{curator|admin|superadmin}/feedback/{cycle_id}`, staff cycle calendars | `cabinet_feedback_detail.html`, `cabinet_staff_cycles.html`, `cabinet_cycle_calendar.html`, JSON/redirects | student, curator, admin, superadmin | `get_current_user`, `require_curator`, `require_superadmin`, `require_csrf`, `get_db` | Диалог ОС. Ручная маршрутизация viewer_role; permission `"feedback.write"` проверяется вручную. |
| `app/api/cabinet_admin.py` | `/cabinet/admin-panel`, `/admin/mock-check`, `/admin/retake-check`, legacy student links, admin score | `cabinet_staff.html`, `cabinet_admin_mock_check.html`, `cabinet_admin_retake_check.html`, redirects/JSON | admin+ | `require_admin_role`, `require_csrf`, `get_db` | Admin dashboard/check screens. Несколько legacy redirects to shared students route. |
| `app/api/cabinet_superadmin.py` | `/cabinet/superadmin`, exam assignments, periods, stats, users CRUD, drive sync retry, curators, impersonation | `cabinet_staff.html`, `superadmin_*`, `periods_management.html`, JSON/redirects | admin+ for many pages, superadmin for high-risk mutations | `require_admin_role`, `require_superadmin`, `require_csrf`, `get_db` | Самый большой router. Есть смешение dashboard, users, tickets, periods, storage retry, impersonation. |
| `app/api/admin.py` | `/admin/users`, user tariff/role/link/toggle/staff/curator/score | `admin_users.html` через `_render_admin_users`, redirects | legacy admin+ | `require_admin`, `require_csrf`, `get_db` | Legacy admin surface. `/admin/users` сейчас тестами ожидаемо redirect/совместим с `/cabinet/superadmin/users`. |
| `app/main.py` direct | `/health`, `/404`, exception handlers | JSON, `404.html`, `blocked.html` | public/system | n/a | Also mounts static and includes all routers. |

## Карта шаблонов

| Template | Используется где | Роль/страница | Похожие шаблоны | Можно ли вынести partial/macro |
|---|---|---|---|---|
| `base.html` | All extending pages | Layout | n/a | Owner для глобальных includes/widgets. Сейчас автоматически включает `staff_nav` только при `user.role_rank >= 4`. |
| `partials/bottom_nav.html` | `cabinet_student`, `cabinet_cycle`, `cabinet_portfolio`, `gallery`, `upload*`, notifications, feedback student view | Student nav mobile+desktop | `staff_nav.html` по механике sidebar/bottom nav | Да, но сохранить student-specific пункты. |
| `partials/staff_nav.html` | auto in `base.html` for rank >=4, manually in `cabinet_staff_cycles.html`, `cabinet_feedback_detail.html`, `cabinet_cycle_calendar.html` | Admin/superadmin staff nav; частично mixed staff pages | `bottom_nav.html`, `_curator_nav.html` | Да: общий renderer/behavior, но role-specific пункты и rank conditions сохранить. |
| `_curator_nav.html` | `cabinet_students.html`, `cabinet_curator_reports.html` | Curator-only top nav when `user.role_rank == 2` | staff nav sections | Можно превратить в role menu config/partial, но не объединять с admin menu по содержанию. |
| `cabinet_staff.html` | `cabinet_admin.py`, `cabinet_superadmin.py` | Admin/superadmin dashboard | old `cabinet_admin.html`, `cabinet_superadmin.html` | Уже общий staff dashboard. Проверять различия rank >=5 blocks. |
| `cabinet_students.html` | `cabinet_students_shared.py` | Curator/admin/superadmin student card/list | old curator/admin student templates | Есть крупные partial candidates: sidebar list/filter, student tag pills already partial, calendar lib already partial. |
| `cabinet_student.html`, `profile.html`, `cabinet_notifications.html` | `cabinet_student.py` | Student dashboard/profile/notifications | upload/student cards | Малые компоненты возможны, крупно объединять не надо. |
| `cabinet_cycle.html`, `cabinet_cycle_calendar.html`, `partials/cycle_day_calendar.html`, `partials/cycle_calendar_lib.html` | student and staff cycle views | Student/staff cycle calendars | repeated cycle widgets | Calendar already partially extracted; next slice can audit remaining duplicated wrappers/back buttons. |
| `upload.html`, `upload_mock.html`, `upload_retake.html` | `upload.py` | Student upload forms | common file upload validation/UI | Можно вынести file upload UI/empty/error/success blocks after checking JS/form names. |
| `cabinet_admin_mock_check.html`, `cabinet_admin_retake_check.html` | `cabinet_admin.py` | Admin check screens | sidebars and split layout | Good candidate for partials: sidebar header/search/list, work cards, score form. |
| `cabinet_curator_portfolio.html`, `cabinet_curator_mockexams.html`, `cabinet_curator_retakes.html` | no direct current `TemplateResponse`; likely legacy | Curator legacy screens | admin check/students sidebar | Проверить ссылки перед любыми изменениями; не удалять в phase 2. |
| `cabinet_curator_reports.html` | `cabinet_curator.py` | Curator/admin reports | staff card/list/buttons | Можно вынести badges/cards after preserving upload/delete contract. |
| `cabinet_feedback_detail.html`, `cabinet_staff_cycles.html` | `feedback.py` | Student/staff feedback dialog/list | cycle calendar/detail cards | Возможны partials для message cards/status badges; осторожно с viewer_role conditions. |
| `superadmin_users.html`, `superadmin_user_card.html`, `admin_users.html` | `cabinet_superadmin.py`, `admin.py` | User management | duplicated filters/forms/actions | Хороший кандидат для service/helper сначала, UI later; права и rank checks критичны. |
| `superadmin_exam_*`, `periods_management.html`, `superadmin_stats.html`, `superadmin_curators.html` | `cabinet_superadmin.py` | Admin/superadmin management | forms/cards/tables | Можно выносить только small UI atoms after route/service audit. |
| `gallery.html`, `history.html`, `3dlab.html`, `login.html`, `staff_login.html`, `denied.html`, `blocked.html`, `404.html` | auth/gallery/main | Mixed/public/student | isolated | Не первые кандидаты для refactor. |
| Not direct current templates: `cabinet.html`, `cabinet_admin.html`, `cabinet_admin_students.html`, `cabinet_admin_student_works.html`, `cabinet_curator.html`, `cabinet_curator_dashboard.html`, `cabinet_superadmin.html`, `scores.html` | no direct `TemplateResponse` found in current routers | likely legacy/dead or included by old flow | n/a | Только проверить ссылками/tests/history; не удалять без отдельного решения. |

## Карта RBAC

| Файл | Тип проверки | Роли/права | Нужно централизовать? | Риск |
|---|---|---|---|---|
| `app/services/rbac.py` | Seed roles/permissions | ranks 1-5: `ученик`, `куратор`, `модератор`, `админ`, `суперадмин`; permissions incl. `feedback.write`, `feedback.view_all` | Уже owner для seed config. Не создавать новый слой до проверки этого файла. | Любое изменение ролей требует обновить dispatcher/dependencies/templates/tests. |
| `app/dependencies.py` | Runtime user dict + dependencies | `get_current_user`, `require_role`, `require_permission`, aliases `require_student/curator/moderator/admin_role/superadmin`, legacy `require_admin` | Да, это runtime owner. `require_permission()` есть, но почти не используется. | Cached session обновляется, но auth-critical state читается из DB. User mutations требуют invalidate_session. |
| `app/api/cabinet.py` | Role dispatcher | `ROLE_CABINET_MAP` by role_name | Да, при изменении roles держать рядом с RBAC. | Модератор сейчас ведёт на `/cabinet/student`; изменение может сломать login redirect. |
| `app/api/cabinet_students_shared.py` | Custom dependency + ownership | `_require_student_panel`: rank 2 or >=4; `_check_access`: curator only own students; rank >=4 all | Можно вынести позже в staff/student access service. | High: IDOR/role semantics. Не менять без tests for curator/admin/superadmin. |
| `app/api/feedback.py` | Manual role routing + permission string | student exact rank 1; curator own students; admin/superadmin; `"feedback.write"` | Да, но отдельным slice после фикса текущего поведения. | High: feedback write/read, redirect prefixes, closed cycle restrictions. |
| `app/api/cabinet_curator.py` | Dependency + manual ownership | `require_curator`, `require_admin_role`, `student.curator_id != user_id and rank < 3`, admin report delete | Частично. Ownership helper может быть shared with student panel/feedback. | Medium/high: curator access to чужие students/reports. |
| `app/api/cabinet_admin.py` | Admin dependency + rank conditions | `require_admin_role`, `is_superadmin = rank >=5` | Частично. | Medium: admin vs superadmin action visibility. |
| `app/api/cabinet_superadmin.py` | Mixed admin/superadmin dependencies + manual rank comparisons | many `require_admin_role`, high-risk mutations use `require_superadmin`; target rank checks | Да, but split after service audit. | High: user management, impersonation, ticket/period management. |
| `app/api/admin.py` | Legacy admin dependency + manual rank checks | `require_admin` via `is_admin`; `acting_rank`, target role rank | Да, with compatibility care. | Medium/high: legacy route tests and redirects. |
| `app/services/user_management.py` | Service-level manage-user rules | `_can_manage_user`: actor rank >=4 and actor rank > target rank | Good owner candidate for block/delete/toggle behavior. | Must align with router rank checks and cache invalidation. |
| Jinja templates | Visibility checks | `user.role_rank >= 4/5`, `user.role_rank < 4`, `viewer_role`, `is_admin_panel`, `is_superadmin` | Centralize menu config first; leave page-specific visibility until owner rules clear. | Medium: UI may show actions backend denies or hide actions backend allows. |

## Карта меню

Важно: меню у каждой роли свое. Этот раздел должен зафиксировать различия до любых изменений.

| Роль | Файл/partial | Пункты меню | Условия показа | Поведение mobile/desktop |
|---|---|---|---|---|
| student | `partials/bottom_nav.html`, included by student templates | Desktop/sidebar + mobile bottom: Кабинет `/cabinet` or `/cabinet/student`, Портфолио `/cabinet/portfolio`, Цикл Пробника `/cabinet/cycle`, Пробник `/upload/mock-exam`, 3D Лаб `/3dlab`, logout in desktop sidebar | Included explicitly in student pages. `base.html` does not auto include it. Uses `active_tab`. | Fixed bottom nav on mobile; desktop `app-sidebar`. Loader JS on nav clicks. |
| curator | `_curator_nav.html` on `cabinet_students.html`/`cabinet_curator_reports.html`; staff cycle/feedback pages include `staff_nav.html` manually | Curator top nav: Ученики `/cabinet/students`, Отчёты `/cabinet/curator/reports`, Статистика `/cabinet/students?tab=statistics`. On staff cycle pages staff-nav adds Кабинет/Ученики/Пробники/Цикл/3D Lab. | `_curator_nav.html` only if `user.role_rank == 2`. `base.html` does not auto include staff nav for rank 2. `staff_nav.html` has extra item `Пробники` only for `user.role_rank < 4`. | `_curator_nav` horizontal top pills; `staff_nav` fixed bottom pill nav on mobile and sidebar on desktop when manually included. |
| admin | `partials/staff_nav.html` auto included from `base.html` for rank >=4 | Кабинет `/cabinet`, Ученики `/cabinet/students`, Цикл Пробника `/cabinet/staff/cycles`, 3D Лаб `/3dlab`, Видео-отчёты `/cabinet/curator/reports`, logout. No `Пробники` item because condition is `user.role_rank < 4`. | `base.html`: `user.role_rank >= 4`; reports item condition `>=4`. Page content has extra admin/superadmin visibility checks. | Desktop `staff-aside`; mobile `staff-pill-nav`. |
| superadmin | `partials/staff_nav.html` auto included from `base.html` for rank >=4 | Same staff-nav visible items as admin: Кабинет, Ученики, Цикл Пробника, 3D Лаб, Видео-отчёты, logout. Superadmin-specific pages/actions live inside dashboard/users/templates, not as separate nav item in current partial. | `base.html`: `user.role_rank >= 4`; high-risk actions gated in routes/templates by `role_rank >= 5` / `require_superadmin`. | Same as admin: desktop sidebar + mobile pill nav. |
| moderator | no dedicated menu found | Dispatcher sends `модератор` to `/cabinet/student`; `_require_student_panel` explicitly denies rank 3. | No role-specific menu found in active templates. | Needs separate product decision before any menu/RBAC change. |

## Карта webhook/storage

| Поток | Endpoint/file | Service | Validation | Tests | Риски |
|---|---|---|---|---|
| Student legacy portfolio upload | `GET/POST /upload`, `POST /upload/api` in `app/api/upload.py` | `app/services/s3.py`, `app/services/n8n.py`, `UploadLog`, `Work`; background `_send_to_n8n_background` | file count/size/type in router; feature gate for after upload; CSRF; S3 failure blocks when configured; n8n failure does not block user success | `tests/test_routes_upload.py`, `tests/test_student_behavior.py`, `tests/test_services_n8n.py` | External n8n JSON contract must not change. Background retry/idempotency uses `work_id`/`idempotency_key`. |
| Student legacy mock/retake upload | `/upload/mock-exam`, `/upload/mock-exam/api`, `/upload/retake`, `/upload/retake/api` in `upload.py` | S3 + n8n background + `ExamCycle` + `MockExamLock/Attempt` | subject, active ticket/feature, size/type/count, score range, CSRF | `tests/test_routes_upload.py`, `tests/test_feedback_dialog.py` | Similar validation to cycle upload but different contract and Drive sync behavior. |
| New cycle upload | `/upload/probnik/final|intermediate`, `/upload/otrabotka/final|intermediate` in `cycle_upload.py` | `s3_service`, `exam_cycle` service, `UploadLog`, `Work` | one final file, up to 10 intermediate, subject/ticket/feature/score, CSRF; `drive_status="s3_only"` | `tests/test_routes_cycle_upload.py`, related upload/feedback tests | Do not add n8n/Drive here accidentally. Repeated logic with upload.py is a refactor candidate. |
| Staff upload for student | `POST /cabinet/students/{student_id}/upload` in `cabinet_students_shared.py` | `s3_service`, `compress_image`, `UploadLog`, `Work`, optional cycle service | admin+ only, file type/size/count, work_type/month/year/subject/score, ownership via `_check_access` | `tests/test_routes_upload.py`, `tests/test_routes_cabinet_curator_new.py` | Duplicates upload validation/S3 path building; high-risk because staff uploads on behalf of user. |
| Feedback photo upload | `POST /cabinet/feedback/{work_id}/message`, `app/services/feedback.py` | `s3_service.s3_path_feedback`, `compress_image`, `FeedbackMessage` | text/photo required, max text 4000, photo size 10MB, cycle open, role/permission/ownership | `tests/test_feedback_dialog.py` | Permission and dialog authorization are mixed in route. Photo type validation is weaker than upload routes. |
| Curator report video | `/cabinet/curator/reports` in `cabinet_curator.py` | `s3_service.s3_path_curator_report`, `CuratorReport`, notifications | video content type, max 500MB, curator role; delete admin+ | `tests/test_curator_reports_stats.py` | Uses S3 video content type; not same as image upload. |
| Ticket image upload | `POST /cabinet/upload-ticket-image` in `cabinet_superadmin.py` | `s3_service`, `compress_image`, `ExamTicket.image_s3_*` | admin+ and CSRF; image MIME/extension/size in route | `tests/test_routes_cabinet_superadmin.py`, upload-related tests | Logic lives in huge superadmin router; candidate for small service/helper after tests. |
| Drive list/sync legacy | `app/services/drive.py`, called by auth login/link and gallery/thumbs | n8n list-photos workflow via `N8N_BASE_URL`; in-memory cache | tg_username required; n8n failures logged and return empty | `tests/test_student_behavior.py`, `tests/test_services_n8n.py` indirectly | Drive API is external n8n workflow. Do not expose secrets; cache invalidation matters after upload. |
| Drive sync retry/status | `/cabinet/superadmin/drive-sync-status`, `/cabinet/superadmin/works/{work_id}/retry-drive-sync` | `send_photo_to_n8n`, `Work.drive_status` | admin+ and CSRF for retry | route tests likely in `tests/test_routes_cabinet_superadmin.py` / upload tests | Re-queue contract must preserve `pending/synced/failed/s3_only`. |

## Решения

Фиксировать архитектурные решения коротко.

| Дата | Решение | Причина | Альтернативы/риски |
|---|---|---|---|
| 2026-06-06 | Вести один файл `AUDIT_REFACTOR_HANDOFF.md` вместо нескольких planning files. | Пользователь попросил один файл для передачи Claude Code/новому чату. | Нужно дисциплинированно обновлять файл после фаз. |
| 2026-06-06 | Burger menu считать role-specific по содержанию. | Пользователь уточнил, что у каждой роли меню свое. | Нельзя бездумно унифицировать список пунктов для всех ролей. |
| 2026-06-06 | В phase 2 начинать не с массового объединения шаблонов, а с маленьких owner-layer slices: RBAC/menu config или upload validation helper только после выбора границы. | Phase 1 показала, что дубли есть, но они связаны с разными контрактами ролей, webhook/storage и UI. | Массовое объединение templates/routes может сломать role-specific меню, feedback/upload contracts и admin/superadmin differences. |
| 2026-06-06 | Для меню сохранять разные пункты по ролям; возможная централизация - данные/renderer/behavior, не единый одинаковый список. | Student, curator, admin/superadmin menus реально отличаются по includes, условиям и пунктам. | Нельзя переносить curator-only `_curator_nav` в staff/admin меню без отдельного UX-решения. |
| 2026-06-06 | Не делать массовое объединение шаблонов как первый refactor step. | Phase 1 показала похожие UI blocks, но многие templates несут разные роли, формы и permissions. | Массовое объединение может скрыто изменить form contracts, active tabs, CSRF и role-specific blocks. |
| 2026-06-06 | Не унифицировать разные меню ролей в один одинаковый список. | Меню student, curator, admin/superadmin отличаются по составу, условиям показа и месту include. | Разрешена только централизация данных/renderer/behavior при сохранении разных списков пунктов. |
| 2026-06-06 | Storage/n8n/Drive трогать только после отдельного контрактного плана. | Legacy upload, cycle upload, staff upload, feedback, curator video, ticket image и Drive retry имеют разные side effects. | Без плана можно сломать n8n JSON, `drive_status`, S3 path, retry/idempotency или Drive cache. |
| 2026-06-06 | RBAC/permissions менять только после отдельного миграционного плана. | Roles/permissions связаны с seed, dispatcher, dependencies, templates, session cache и route tests. | Нельзя менять ranks, role names, aliases `require_*` и `ROLE_CABINET_MAP` как обычный small cleanup. |

## Измененные файлы

Фиксировать только фактически измененные файлы.

| Фаза | Файл | Что изменено | Агент/дата |
|---|---|---|---|
| 0 | `AUDIT_REFACTOR_HANDOFF.md` | Создан план, протокол хенд оффа и чеклисты фаз. | Codex / 2026-06-06 |
| 1 | `AUDIT_REFACTOR_HANDOFF.md` | Заполнены карты аудита: роутеры, шаблоны, RBAC, меню, webhook/storage; фаза 1 отмечена done. | Codex / 2026-06-06 |
| 2 | `AUDIT_REFACTOR_HANDOFF.md` | Добавлен архитектурный план маленьких refactor slices P1/P2/P3, план проверки и инструкции для следующего агента; код приложения не менялся. | Codex / 2026-06-06 |
| 3 / P1-01 | `tests/test_navigation_contracts.py` | Добавлены contract tests для `/cabinet` redirects, student/curator/admin/superadmin меню и текущего moderator behavior. | Codex / 2026-06-06 |
| 3 / P1-01 | `AUDIT_REFACTOR_HANDOFF.md` | Зафиксировано выполнение первого slice, результаты тестов и следующий безопасный шаг. | Codex / 2026-06-06 |
| 3 / P1-02 | `app/templates/partials/exam_assignment_status.html` | Добавлен macro `exam_assignment_status_badge(status)` для единого рендера статуса задания. | Codex / 2026-06-06 |
| 3 / P1-02 | `app/templates/superadmin_exam_assignments.html` | Подключен macro вместо локального `status_labels` и inline status span. | Codex / 2026-06-06 |
| 3 / P1-02 | `app/templates/superadmin_exam_assignment_detail.html` | Подключен тот же macro для статуса задания на detail page. | Codex / 2026-06-06 |
| 3 / P1-02 | `tests/test_exam_assignment_templates.py` | Добавлены render-contract tests для status badge на list/detail pages. | Codex / 2026-06-06 |
| 3 / P1-02 | `AUDIT_REFACTOR_HANDOFF.md` | Зафиксировано выполнение P1-02 и результаты проверок. | Codex / 2026-06-06 |
| 3 / P1-03 | `app/services/upload_validation.py` | Добавлен общий helper `is_allowed_image` / `read_image_uploads` и upload validation constants. | Codex / 2026-06-06 |
| 3 / P1-03 | `app/api/upload.py` | Повторяющиеся проверки файлов заменены на `_validate_photos`, который вызывает общий helper; upload processing/S3/n8n/Drive не менялись. | Codex / 2026-06-06 |
| 3 / P1-03 | `app/api/cycle_upload.py` | `_read_photos` переведен на общий helper; cycle upload остается `s3_only`, save pipeline не менялся. | Codex / 2026-06-06 |
| 3 / P1-03 | `tests/test_upload_validation.py` | Добавлены unit tests для allowed image detection, чтения файлов и route-specific format message. | Codex / 2026-06-06 |
| 3 / P1-03 | `AUDIT_REFACTOR_HANDOFF.md` | Зафиксировано выполнение P1-03, результаты проверок и следующий безопасный шаг. | Codex / 2026-06-06 |
| 3 / P2-01a | `app/services/navigation.py` | Добавлены `NavItem`, `CURATOR_NAV_ITEMS`, `curator_nav_items()` как первый role-specific menu config. | Codex / 2026-06-07 |
| 3 / P2-01a | `app/tmpl.py` | `curator_nav_items` зарегистрирован как Jinja global для шаблонов. | Codex / 2026-06-07 |
| 3 / P2-01a | `app/templates/_curator_nav.html` | Hardcoded curator links заменены циклом по `curator_nav_items()` без изменения role guard, CSS classes, hrefs и labels. | Codex / 2026-06-07 |
| 3 / P2-01a | `tests/test_navigation_service.py` | Добавлен contract test для состава curator menu config. | Codex / 2026-06-07 |
| 3 / P2-01a | `AUDIT_REFACTOR_HANDOFF.md` | Зафиксировано частичное выполнение P2-01a и граница следующего menu slice. | Codex / 2026-06-07 |
| 3 / P2-01b | `app/services/navigation.py` | Добавлены `StudentNavItem`, `STUDENT_NAV_ITEMS`, `student_nav_items()` как отдельный student menu config. | Codex / 2026-06-07 |
| 3 / P2-01b | `app/tmpl.py` | `student_nav_items` зарегистрирован как Jinja global для student partial. | Codex / 2026-06-07 |
| 3 / P2-01b | `app/templates/partials/bottom_nav.html` | Desktop/mobile student ссылки заменены циклами по `student_nav_items()`; SVG оставлены в локальном Jinja macro, CSS/JS/classes не менялись. | Codex / 2026-06-07 |
| 3 / P2-01b | `tests/test_navigation_service.py` | Добавлен contract test для состава student menu config. | Codex / 2026-06-07 |
| 3 / P2-01b | `AUDIT_REFACTOR_HANDOFF.md` | Зафиксировано выполнение student bottom nav config и следующий staff-only menu slice. | Codex / 2026-06-07 |
| 3 / P2-01c | `app/services/navigation.py` | Добавлены `StaffNavItem`, `STAFF_NAV_ITEMS`, `staff_nav_items(role_rank)` и rank visibility для staff menu без смешивания с student/curator configs. | Codex / 2026-06-07 |
| 3 / P2-01c | `app/tmpl.py` | `staff_nav_items` зарегистрирован как Jinja global для `partials/staff_nav.html`. | Codex / 2026-06-07 |
| 3 / P2-01c | `app/templates/partials/staff_nav.html` | Desktop `staff-aside` и mobile `staff-pill-nav` переведены на цикл по `staff_nav_items(user.role_rank)`; SVG оставлены в локальном macro, CSS/JS/classes/URLs/active conditions сохранены. | Codex / 2026-06-07 |
| 3 / P2-01c | `tests/test_navigation_service.py` | Добавлены contract tests для admin staff menu и rank-specific visibility `mock_check`/`reports`. | Codex / 2026-06-07 |
| 3 / P2-01c | `AUDIT_REFACTOR_HANDOFF.md` | Зафиксировано закрытие P2-01 и следующий кандидат P2-02. | Codex / 2026-06-07 |

## Проверки

Фиксировать команды и результат.

| Дата | Команда | Результат | Комментарий |
|---|---|---|---|
| 2026-06-06 | Не запускались | n/a | На этом шаге создан только план. |
| 2026-06-06 | `Get-Content -Encoding UTF8` для `AUDIT_REFACTOR_HANDOFF.md`, `../CLAUDE.md`, `CLAUDE.md` | ok | Прочитаны инструкции перед фазой 1. |
| 2026-06-06 | `rg`/AST scan по `app/main.py`, `app/api`, `app/templates`, `app/services`, `tests` | ok | Собраны карты routers/templates/RBAC/menu/webhook-storage. Код приложения не менялся. |
| 2026-06-06 | `pytest` | not run | Phase 1 - документированный аудит без изменений кода; поведенческие тесты запускать на phase 2/после code changes. |
| 2026-06-06 | `Get-Content -Encoding UTF8` для `../AGENTS.md`, `../CLAUDE.md`, `CLAUDE.md`, `AUDIT_REFACTOR_HANDOFF.md`; `rg` по routes/templates/RBAC/storage markers | ok | Phase 2: проверены инструкции, статус phase 1, закрытый чеклист и наличие карт routers/templates/RBAC/menu/webhook-storage. |
| 2026-06-06 | `rg phase_2_done` и `rg` по чеклисту/картам; `git status --short -- AUDIT_REFACTOR_HANDOFF.md` | ok | Подтвержден статус `phase_2_done`, закрытие phase 2 checklist и статус файла `?? AUDIT_REFACTOR_HANDOFF.md` в dirty worktree. |
| 2026-06-06 | `pytest` | not run | Phase 2 меняла только planning document; код приложения, `.env`, routes, templates, RBAC, S3, Drive, n8n не менялись. |
| 2026-06-06 | `pytest tests/test_navigation_contracts.py` | 10 passed | P1-01: новые contract tests прошли. Есть только существующие deprecation warnings Pydantic/slowapi/Starlette templates. |
| 2026-06-06 | `pytest tests/test_routes_cabinet.py tests/test_routes_login.py tests/test_navigation_contracts.py` | 28 passed, 2 skipped | Проверен обязательный набор для P1-01; приложение не менялось. |
| 2026-06-06 | `pytest tests/test_exam_assignment_templates.py tests/test_routes_cabinet_superadmin.py` | 26 passed | P1-02: status badge macro и superadmin routes render contracts. Есть только существующие deprecation warnings. |
| 2026-06-06 | `pytest tests/test_navigation_contracts.py tests/test_routes_cabinet.py tests/test_routes_login.py` | 28 passed, 2 skipped | Защитный P1-01 набор после template refactor; role-specific menu contracts не изменились. |
| 2026-06-06 | `pytest tests/test_upload_validation.py tests/test_routes_upload.py tests/test_routes_cycle_upload.py` | 62 passed | P1-03: pure upload validation helper, legacy upload и cycle upload contracts. Есть только существующие deprecation warnings. |
| 2026-06-06 | `pytest tests/test_routes_cabinet_superadmin.py tests/test_routes_cabinet_curator_new.py` | 40 passed | P1-03 соседние staff/curator route checks по плану проверки. Есть только существующие deprecation warnings. |
| 2026-06-06 | `pytest tests/test_navigation_contracts.py tests/test_routes_cabinet.py tests/test_routes_login.py` | 28 passed, 2 skipped | Защитный navigation набор после upload helper; role-specific dispatcher/menu не изменились. |
| 2026-06-06 | `python -m compileall app tests` | ok | Синтаксическая проверка после Python refactor. |
| 2026-06-07 | `pytest tests/test_navigation_service.py tests/test_navigation_contracts.py tests/test_routes_cabinet.py tests/test_routes_login.py` | 29 passed, 2 skipped | P2-01a: curator menu config и общий navigation contract. Есть только существующие deprecation warnings. |
| 2026-06-07 | `pytest tests/test_routes_cabinet_curator_new.py tests/test_curator_reports_stats.py` | 25 passed | P2-01a: страницы, где подключается `_curator_nav.html`, render без изменения доступа/меню. Есть только существующие deprecation warnings. |
| 2026-06-07 | `python -m compileall app tests` | ok | Синтаксическая проверка после navigation service/template refactor. |
| 2026-06-07 | `pytest tests/test_navigation_service.py tests/test_navigation_contracts.py tests/test_routes_cabinet.py tests/test_routes_login.py` | 30 passed, 2 skipped | P2-01b: student menu config и общий navigation contract. Есть только существующие deprecation warnings. |
| 2026-06-07 | `pytest tests/test_student_behavior.py tests/test_routes_upload.py tests/test_feedback_dialog.py` | 100 passed | P2-01b: страницы с `bottom_nav.html` и соседние student/upload/feedback flows. Есть только существующие deprecation warnings. |
| 2026-06-07 | `python -m compileall app tests` | ok | Синтаксическая проверка после student bottom nav refactor. |
| 2026-06-07 | `pytest tests/test_navigation_service.py tests/test_navigation_contracts.py tests/test_routes_cabinet.py tests/test_routes_login.py` | 32 passed, 2 skipped | P2-01c: staff menu config, rank visibility и общий navigation contract. Есть только существующие deprecation warnings. |
| 2026-06-07 | `pytest tests/test_routes_cabinet_superadmin.py tests/test_routes_cabinet_curator_new.py tests/test_feedback_dialog.py` | 68 passed | P2-01c: страницы с auto/manual `staff_nav.html`, superadmin/curator/feedback flows. Есть только существующие deprecation warnings. |
| 2026-06-07 | `python -m compileall app tests` | ok | Синтаксическая проверка после staff nav refactor. |

## Ошибки и блокеры

| Дата | Проблема | Что пробовали | Текущее состояние |
|---|---|---|---|

## Следующий агент: что делать дальше

1. Прочитать `../CLAUDE.md`, `CLAUDE.md` и этот файл.
2. Не трогать незнакомые незакоммиченные изменения; текущий worktree dirty до этой фазы.
3. P1-01, P1-02, P1-03 и P2-01a/b/c выполнены и проверены. Перед любым следующим refactor запускать релевантный baseline из `План проверки`.
4. Следующий кандидат: P2-02 staff/student access helper для ownership checks. Начинать с чтения `app/api/cabinet_students_shared.py`, `app/api/cabinet_curator.py`, `app/api/feedback.py`, `tests/test_routes_cabinet_curator_new.py`, `tests/test_feedback_dialog.py`, `tests/test_routes_cabinet_superadmin.py`.
5. В P2-02 нельзя менять, какие роли получают доступ, redirect/status code при отказе, тексты ошибок, feedback closed-cycle rules и IDOR semantics. Сначала добавить/уточнить contract tests на текущие разрешенные и запрещенные сценарии, потом заменять один route/helper.
6. Файлы, которые нельзя трогать без дополнительного анализа: `.env`, `.env.deploy`, `docker-compose*.yml`, `scripts/deploy.py`, `app/services/rbac.py`, `app/dependencies.py`, `app/services/n8n.py`, `app/services/drive.py`, `app/services/s3.py`, migrations, storage save pipeline и upload side effects.
7. Не начинать с массового объединения шаблонов. Legacy templates без прямого `TemplateResponse` не удалять без отдельной проверки ссылок, tests и истории.
8. Для меню сохранить role-specific состав: student `bottom_nav`, curator `_curator_nav`, admin/superadmin `staff_nav`; не превращать их в один одинаковый список. P2-01 уже вынес данные меню в отдельные configs, но общий renderer для всех ролей не вводился.
9. Перед любым code change выбрать owner layer, affected routes/templates/tests и минимальный набор проверок из раздела `План проверки`.
