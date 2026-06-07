# CLAUDE.md

Полное описание архитектуры, RBAC, моделей и инфраструктурных правил — в родительском `../CLAUDE.md`. Читай его перед нетривиальными правками.

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

См. полный список guardrails и context в `../CLAUDE.md`.
