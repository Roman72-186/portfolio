#!/bin/bash
# Ночной дамп базы Apparchi (код-ревью 28.09.2026, раздел «Бэкапы»).
#
# Раньше вся логика жила одной строкой в /etc/cron.d/portfolio-pgbackup:
# `pg_dump | gzip > файл && find ... -delete`. Без pipefail упавший pg_dump
# оставлял маленький gzip, а задание отчитывалось успехом — сбой никто бы не
# заметил, пока копия не понадобится.
#
# Установка на сервер (139.100.237.57, под root):
#   install -m 750 scripts/server/portfolio-pgbackup.sh /usr/local/bin/portfolio-pgbackup
#   строка в /etc/cron.d/portfolio-pgbackup:
#   30 3 * * * root /usr/local/bin/portfolio-pgbackup
#   и один ручной прогон: /usr/local/bin/portfolio-pgbackup
#
# Итог каждого запуска пишется в журнал: journalctl -t portfolio-pgbackup
set -euo pipefail

DIR=/var/backups/portfolio
KEEP_DAYS=14
# Живой дамп на 29.09.2026 — 1,4 МБ. Меньше порога — pg_dump упал.
MIN_BYTES=102400

f="$DIR/portfolio-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
trap 'rm -f "$f"; logger -t portfolio-pgbackup "ОШИБКА: дамп не снят"' ERR

mkdir -p "$DIR"
docker exec portfolio-saas-db-1 pg_dump -U portfolio -d portfolio --clean --if-exists | gzip > "$f"
gzip -t "$f"
s=$(stat -c %s "$f")
if [ "$s" -lt "$MIN_BYTES" ]; then
    rm -f "$f"
    logger -t portfolio-pgbackup "ОШИБКА: дамп подозрительно мал ($s байт), удалён"
    exit 1
fi
# Дамп проверен — сбой ротации ниже не должен удалить его.
trap - ERR
find "$DIR" -name 'portfolio-*.sql.gz' -mtime +"$KEEP_DAYS" -delete || logger -t portfolio-pgbackup "ротация старых дампов не удалась"
logger -t portfolio-pgbackup "ok: $f ($s байт)"

# Копия вне сервера (решение владельца 29.09.2026: отдельный бакет Selectel).
# Без неё потеря или взлом VPS уносят и базу, и все копии разом.
# Включается файлом /etc/portfolio-pgbackup.env со строкой
#   OFFSITE_REMOTE=apparchi-backup:<бакет>/daily
# где `apparchi-backup` — отдельный remote rclone со своим ключом, который
# умеет только класть файлы: ключ приложения и этот ключ не должны совпадать,
# иначе взломанный сервер сотрёт и копии. Срок хранения в бакете задаёт
# правило жизненного цикла в панели Selectel, а не этот скрипт — удалять ключ
# бэкапа не умеет намеренно. Секретов в env-файле нет: ключ живёт в
# /root/.config/rclone/rclone.conf. --s3-no-check-bucket, --no-check-dest и
# --s3-no-head: ключу разрешена только загрузка (s3:PutObject), проверять бакет,
# наличие файла и читать его после загрузки ему нельзя. Проверено 29.09.2026:
# чтение, удаление и список этим ключом дают 403.
#
# rclone — отдельный свежий бинарник /opt/portfolio-backup/rclone (1.75.1 на
# 29.09.2026, с downloads.rclone.org, sha256 сверен). Системный 1.53 из Ubuntu
# не знает --s3-no-head и после загрузки читает объект — под ключом «только
# запись» это 403, и загрузка считалась неудачной. Системный не трогаем.
RCLONE=${RCLONE:-/opt/portfolio-backup/rclone}
if [ -r /etc/portfolio-pgbackup.env ]; then
    # shellcheck disable=SC1091
    . /etc/portfolio-pgbackup.env
fi
if [ -n "${OFFSITE_REMOTE:-}" ]; then
    if "$RCLONE" copyto --s3-no-check-bucket --no-check-dest --s3-no-head "$f" "$OFFSITE_REMOTE/$(basename "$f")"; then
        logger -t portfolio-pgbackup "ok: копия вне сервера — $OFFSITE_REMOTE/$(basename "$f")"
    else
        logger -t portfolio-pgbackup "ОШИБКА: копия вне сервера не ушла ($OFFSITE_REMOTE), локальная цела"
        exit 2
    fi
fi
