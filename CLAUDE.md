# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Ядро проекта и критичные правила инфраструктуры — в `../AGENTS.md` (родительский `../CLAUDE.md` — только указатель на него). Архитектура, RBAC, модели, интеграции и устройство тестов — в `../docs/architecture.md`, читать по требованию. Стиль кода и правила PR — в соседнем `AGENTS.md`. Дизайн-система и Spark-классы — в `DESIGN.md`.

Перед правкой роута, сервиса или модели — `.codegraph/` (индексированный граф кода): `codegraph_explore`/`codegraph_search` находят определение и использования, `codegraph_impact`/`codegraph_callers` показывают, что сломается. Индекс обновляется сам.

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

## Жёсткие правила — в `../AGENTS.md`

Раздел «Критичные правила инфраструктуры» и следом «Инварианты, которые не видно
из одного файла». Там же причина и прецедент у каждого правила — здесь их держать
незачем: файл лежит в отдельном git-репозитории и одним коммитом с владельцем
не поедет, поэтому копия неизбежно отстаёт. До 10.09.2026 тут висела выдержка
из восьми пунктов (SAVEPOINT, Traefik, `invalidate_session`, импорт `app.main`
в тестах, ранги RBAC, `tz.py`, ключи Bunny, прод-compose) — все восемь целы
в `../AGENTS.md`, сверены поимённо.

**Новое правило пишется в `../AGENTS.md`.** Параллельный список здесь не заводить.

Архитектура, модели, роутеры и устройство тестов — `../docs/architecture.md`.
