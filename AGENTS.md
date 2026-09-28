# Repository Guidelines

## Project Structure & Module Organization

This repository is a FastAPI/Jinja2 application. Runtime code lives in `app/`:
`app/main.py` wires the application, `app/api/` contains route modules,
`app/services/` holds business logic, `app/models/` and `app/db/` define data
access, `app/templates/` contains Jinja templates, and `app/static/` contains
CSS, JS, images, and fonts. Database migrations are in `alembic/`. Tests are in
`tests/`, with shared fixtures in `tests/conftest.py`. Operational scripts live
in `scripts/`; reports and handoff notes are kept in `reports/` and
`session-handoffs/`.

## Build, Test, and Development Commands

- `python -m pip install -r requirements.txt` installs Python dependencies.
- `python -m pytest tests/test_routes_login.py` runs a focused test file.
- `python -m pytest` runs the full suite (2000+ tests, minutes) — only on the
  owner's `/itog` command. Without `.env` set `SESSION_SECRET=<any string>`
  first, otherwise importing `app.main` fails.
- `python -m pytest tests/test_reuse_ratchet.py` — reuse ratchet (no new inline
  CSS, hardcoded colors, or overrides of shared classes).
- There is no linter or type checker in the project; do not look for one.
- `docker compose up --build` starts the app, Redis, and Postgres locally.
- `alembic upgrade head` applies database migrations.
- `python -m uvicorn app.main:app --reload` runs the app directly; set
  `DATABASE_URL`, `SESSION_SECRET`, and related env vars first.

## Coding Style & Naming Conventions

Use 4-space indentation for Python and keep route handlers thin: request parsing
belongs in `app/api/`, reusable logic in `app/services/`. Name tests
`test_*.py` and test functions `test_<behavior>`. For templates, preserve
existing Jinja patterns and Spark design classes from `app/static/css/base.css`
such as `.btn-blue`, `.btn-outline`, `.btn-danger`, and `.back-link`. Do not add
new dependencies or rename public routes/API fields without a clear need.

## Testing Guidelines

Pytest is the primary test framework. Add or update focused tests for route,
template, service, and permission changes. Prefer targeted runs during feature
work, then broader suites before handoff when risk is high. For UI/template
changes, combine route tests with grep checks or a small Playwright smoke when
available.

## Commit & Pull Request Guidelines

Commit messages are written in Russian, without Conventional Commit prefixes,
and name the user-visible change, for example `Список учеников снова виден:
вернул закрывающий </script>`. Older history still has `feat:`/`fix:` prefixes;
do not continue that style. Keep commits focused and stage files by path —
`git add -A` and `git add .` are forbidden.
Pull requests should include a short summary, changed screens/routes, test
commands and results, screenshots for visual changes, and any migration or env
var notes.

## Security & Configuration Tips

Never commit `.env`, tokens, passwords, service keys, or personal data. Add new
configuration keys to `.env.example` only. Payment, auth, RBAC, webhook, and
production deployment logic require extra review; do not deploy or run
`scripts/deploy.py` without explicit owner approval.
