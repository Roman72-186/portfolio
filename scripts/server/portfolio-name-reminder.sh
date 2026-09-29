#!/bin/bash
# Разовая кампания 29.09–06.10.2026: напоминание ученикам вписать ФИО как в
# паспорте. Логика и список учеников — name-reminder.py рядом; после 06.10
# скрипт ничего не делает, строку cron можно удалить.
#
# Установка на сервер (139.100.237.57, под root):
#   install -D -m 644 scripts/server/name-reminder.py /usr/local/lib/portfolio/name-reminder.py
#   install -m 750 scripts/server/portfolio-name-reminder.sh /usr/local/bin/portfolio-name-reminder
#   /etc/cron.d/portfolio-name-reminder (644, root):
#   0 9 * * * root /usr/local/bin/portfolio-name-reminder
#   проверка без отправки: /usr/local/bin/portfolio-name-reminder --dry-run
#   (обычный ручной прогон в день установки НЕ делать — пришлёт второе
#   сообщение; от этого страхует пауза 44 часа в самом скрипте)
#
# Журнал: journalctl -t portfolio-name-reminder
# Снять: rm /etc/cron.d/portfolio-name-reminder
set -o pipefail

# Скрипт подаётся через stdin: в контейнер его не кладём, чтобы не
# пересобирать образ ради разовой задачи. Без -i stdin до python не дойдёт.
docker exec -i portfolio-saas-app-1 python - "$@" \
    < /usr/local/lib/portfolio/name-reminder.py 2>&1 \
    | logger -s -t portfolio-name-reminder
