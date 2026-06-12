# Архив мёртвых шаблонов

Сюда вынесены Jinja-шаблоны, на которые **нет ни одной ссылки** в актуальном коде:
ни одного `TemplateResponse(...)` в `app/`, ни одного `{% extends %}` / `{% include %}`
в живых шаблонах, ни одной ссылки в `tests/`, и нет динамической сборки имён шаблонов.

Папка лежит **вне** `app/templates/`, поэтому Jinja её не сканирует. Файлы сохранены
(а не удалены) для истории и возможного отката — они также есть в git-истории.

## Что вынесено (2026-06-12)

Листовые страницы (`extends base.html`, никто их не рендерит и не включает):
- `cabinet.html`
- `cabinet_admin.html`
- `cabinet_admin_students.html`
- `cabinet_admin_student_works.html`
- `cabinet_curator.html`
- `cabinet_curator_dashboard.html`
- `cabinet_curator_mockexams.html`
- `cabinet_curator_portfolio.html`
- `cabinet_curator_retakes.html`
- `cabinet_superadmin.html`
- `scores.html`

Партиал без единой ссылки:
- `partials/cycle_back.html`

## Чем заменены (живые аналоги)

- Дашборды admin/superadmin → `cabinet_staff.html`.
- Карточки/списки учеников для staff → `cabinet_students.html` (`cabinet_students_shared.py`).
- Экраны проверки → `cabinet_admin_mock_check.html`, `cabinet_admin_retake_check.html`.
- Отчёты куратора → `cabinet_curator_reports.html`.

## Как вернуть файл обратно

```bash
git mv _legacy_templates/<файл> app/templates/<путь>
```
