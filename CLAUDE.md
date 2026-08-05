# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Ядро проекта и критичные правила инфраструктуры — в `../AGENTS.md` (родительский `../CLAUDE.md` — только указатель на него). Архитектура, RBAC, модели, интеграции и устройство тестов — в `../docs/architecture.md`, читать по требованию. Стиль кода и правила PR — в соседнем `AGENTS.md`.

## Quick commands (run from this directory)

```bash
pytest                                          # all tests (SQLite in-memory)
pytest tests/test_routes_auth.py::test_name     # single test

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload   # local run

alembic upgrade head                            # apply migrations
alembic revision --autogenerate -m "msg"        # new migration

set -a && source .env.deploy && set +a && python scripts/deploy.py   # deploy to prod (139.100.237.57)
python scripts/deploy.py app/api/video.py app/services/video_catalog.py  # только эти файлы
python scripts/deploy.py --sync-env                 # + залить окружение из .env.prod
python scripts/deploy.py --status                   # версия на проде + сверка всех файлов
```

⚠️ **Прод не расходится с коммитом.** Полный деплой требует чистого дерева, поштучный — чтобы заливаемые файлы были закоммичены; иначе отказ. Обход — `--allow-dirty`, и тогда деплой помечается грязным. После каждой заливки на сервер пишется `.deployed-version` (коммит, ветка, время, режим, список файлов). `--status` показывает версию прода против локального HEAD и сверяет sha256 всех отслеживаемых файлов — это единственный способ узнать наверняка, что на сервере.

⚠️ `.env.deploy` по умолчанию всё ещё указывает на `89.23.96.254` — прежний хост Apparchi, который сейчас держит **чужой боевой проект FitMatch**. Деплой без переопределения ударит по нему. Перед деплоем явно задавать `PORTFOLIO_SSH_HOST=139.100.237.57` и `PORTFOLIO_SSH_KNOWN_HOSTS=deploy_known_hosts`.

⚠️ **Окружение сервера и локальный `.env` — разные вещи.** `.env` в этой папке — dev-конфигурация: на 2026-08-05 из 33 переменных 32 расходились с боевыми, включая `DATABASE_URL`, `POSTGRES_PASSWORD`, `SESSION_SECRET`, `REDIS_PASSWORD`, ключи S3 и VK. До 05.08 `deploy.py` заливал `.env` на сервер **при каждом запуске, включая поштучный режим** — это положило бы прод и разлогинило всех учеников. Теперь `.env` сервера не трогается без явного `--sync-env`, а источник правды для прода — отдельный `.env.prod` (создаёт владелец, в git не попадает: скрипт отказывается работать с отслеживаемым файлом). `--sync-env` делает бэкап `.env.bak-<UTC>` на сервере, печатает разницу **по именам переменных** и отказывается, если ключ пропадает (обход — `--allow-remove-env-keys`).

**Полный деплой заливает всё дерево** кроме `.git`/`__pycache__`/`.env`/`tests`/`venv`/`node_modules` — вместе с любым незакоммиченным чужим WIP, включая боевую статику `app/static/`. Если в репозитории лежат чужие правки, деплоить поштучно.

## Жёсткие правила (выдержка)

- **Seed в lifespan — только через SAVEPOINT** (`with db.begin_nested():` на каждую запись), иначе `IntegrityError` отравит транзакцию и старт упадёт.
- **Traefik**: у `app` несколько docker-сетей — обязателен label `traefik.docker.network=web`.
- **После любой мутации полей User** вызывай `app.cache.invalidate_session(session_id)` — иначе Redis-кэш отдаёт устаревший user dict.
- **Прод-compose**: `docker-compose.prod-ru.yml` (в нём встроен Traefik). `deploy.py` после билда сам делает `FLUSHDB` в Redis.
- **Тесты не импортируют `app.main` на уровне модуля** — `conftest.py` подменяет движок на SQLite до импорта; импорт вне фикстуры привяжет тест к боевому PostgreSQL.
- **RBAC — только по рангам роли** (1–5). Permissions удалены миграцией `375d357fbd05`; `require_permission` и `ROLE_PERMISSIONS` в коде нет, не восстанавливать.
- **Даты и периоды — через `app/services/tz.py`** (`today_msk()`/`now_msk()`), не `date.today()`: в контейнере UTC, иначе фильтры едут на 3 часа.
- **Видео: ключи Bunny только на сервере.** Браузер получает подписанный embed-URL с TTL и временные TUS-креденшелы; `BUNNY_STREAM_API_KEY`/`BUNNY_STREAM_TOKEN_KEY` не попадают в HTML, логи и коммиты. Файл идёт напрямую браузер → Bunny, мимо VPS. Модуль — `app/services/bunny_stream.py`, обзор — `../session-handoffs/video-integration.md`.

См. полный список guardrails в `../AGENTS.md`, архитектуру — в `../docs/architecture.md`.
