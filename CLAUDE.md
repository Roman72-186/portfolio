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

set -a && source .env.deploy && set +a && python scripts/deploy.py   # deploy to prod (89.23.96.254)
```

## Жёсткие правила (выдержка)

- **Seed в lifespan — только через SAVEPOINT** (`with db.begin_nested():` на каждую запись), иначе `IntegrityError` отравит транзакцию и старт упадёт.
- **Traefik**: у `app` несколько docker-сетей — обязателен label `traefik.docker.network=web`.
- **После любой мутации полей User** вызывай `app.cache.invalidate_session(session_id)` — иначе Redis-кэш отдаёт устаревший user dict.
- **Прод-compose**: `docker-compose.prod-ru.yml` (в нём встроен Traefik). `deploy.py` после билда сам делает `FLUSHDB` в Redis.
- **Тесты не импортируют `app.main` на уровне модуля** — `conftest.py` подменяет движок на SQLite до импорта; импорт вне фикстуры привяжет тест к боевому PostgreSQL.
- **RBAC — только по рангам роли** (1–5). Permissions удалены миграцией `375d357fbd05`; `require_permission` и `ROLE_PERMISSIONS` в коде нет, не восстанавливать.
- **Даты и периоды — через `app/services/tz.py`** (`today_msk()`/`now_msk()`), не `date.today()`: в контейнере UTC, иначе фильтры едут на 3 часа.

См. полный список guardrails в `../AGENTS.md`, архитектуру — в `../docs/architecture.md`.
