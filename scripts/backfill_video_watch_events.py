"""
backfill_video_watch_events.py — перенос срезов и отказов зачёта видео из
журнала сервера в таблицу `video_watch_events` (миграция `32872b61da2f`).

До 07.10.2026 причины незачёта писались только строками журнала:

    Видео: кусок не засчитан | user=… | video=… | позиция A→B | срезано=… |
        пауза между отметками=… | играло=… | засчитано всего=…
    Видео не засчитано, кружок не поставлен | block=… | user=… | причина=…

Строки пишутся с 06.10.2026, журнал (journald) живёт на сервере с 05.10 и
вытесняется. Скрипт читает вывод `journalctl -o short-iso` со стандартного
входа, раскладывает срез по причине тем же `classify_cut`, что и приложение,
и вставляет строки с временем из журнала.

Две неточности переноса. «Засчитано до шага» восстанавливается как «засчитано
всего» минус засчитанный прирост; после прошлого зачёта ролика приложение
считает от отметки последнего зачёта — для перенесённых строк это различие
теряется. Ролик отказа берётся из блока (`task_blocks.video_id`), в строке
журнала его нет; блок удалён — строка пропускается.

Повторный запуск не дублирует: строка с тем же учеником, роликом, видом,
причиной и секундой времени не вставляется.

Запуск на проде — журнал читает хост, пишет контейнер:

    journalctl -t apparchi-app --since '2026-10-06' -o short-iso --no-pager \\
        | docker exec -i -w /app -e PYTHONPATH=/app portfolio-saas-app-1 \\
            python scripts/backfill_video_watch_events.py

    … то же с --apply в конце — записать.

Без `--apply` ничего не пишет: показывает, сколько строк по каким причинам
будет перенесено.
"""
import argparse
import collections
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import SessionLocal  # noqa: E402
from app.models.learning_video import LearningVideo  # noqa: E402
from app.models.task_block import TaskBlock  # noqa: E402
from app.models.video_watch_event import VideoWatchEvent  # noqa: E402
from app.services.video_watch_events import (  # noqa: E402
    KIND_CUT,
    KIND_REFUSAL,
    REFUSAL_BELOW_THRESHOLD,
    REFUSAL_COMPLETED_BEFORE_BLOCK,
    REFUSAL_NO_DURATION,
    REFUSAL_NO_VIDEO,
    REFUSAL_NOT_STARTED,
    REFUSAL_SHORTFALL,
    classify_cut,
)

STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4})")
CUT = re.compile(
    r"кусок не засчитан \| user=(\d+) \| video=(\S+) \| позиция (\d+)→(\d+)"
    r" \| срезано=(\d+) \| пауза между отметками=(\S+) \| играло=(\w+)"
    r" \| засчитано всего=(\d+)"
)
REFUSAL = re.compile(r"кружок не поставлен \| block=(\d+) \| user=(\d+) \| причина=(.*)$")
NUMBER = re.compile(r"(позиция|честных|длительность)=(\d+)")
# Начало текста причины в журнале → ключ (тексты `_video_watch_refusal`).
REFUSAL_PREFIXES = (
    ("ролика нет", REFUSAL_NO_VIDEO),
    ("ролик не запускался", REFUSAL_NOT_STARTED),
    ("засчитан до создания блока", REFUSAL_COMPLETED_BEFORE_BLOCK),
    ("у ролика нет длительности", REFUSAL_NO_DURATION),
    ("не досмотрел до порога", REFUSAL_BELOW_THRESHOLD),
    ("дошёл до конца", REFUSAL_SHORTFALL),
)


def parse(lines, video_by_block):
    for line in lines:
        stamp = STAMP.match(line)
        if not stamp:
            continue
        at = datetime.strptime(stamp.group(1), "%Y-%m-%dT%H:%M:%S%z")
        m = CUT.search(line)
        if m:
            user, video, a, b, cut, gap, playing, total = m.groups()
            a, b, cut, total = float(a), float(b), float(cut), float(total)
            gap = None if gap == "?" else float(gap)
            playing = playing == "True"
            yield VideoWatchEvent(
                user_id=int(user), video_id=video, kind=KIND_CUT, created_at=at,
                reason=classify_cut(
                    position_from=a, position_to=b, skipped_seconds=cut,
                    gap_seconds=gap, playing=playing,
                    credited_before=total - ((b - a) - cut),
                ),
                position_from=a, position_to=b, skipped_seconds=cut,
                gap_seconds=gap, playing=playing, watched_seconds=total,
            )
            continue
        m = REFUSAL.search(line)
        if m:
            block, user, text = int(m.group(1)), int(m.group(2)), m.group(3).strip()
            video = video_by_block.get(block)
            if video is None:
                continue
            reason = next((key for prefix, key in REFUSAL_PREFIXES if text.startswith(prefix)), None)
            if reason is None:
                continue
            numbers = {k: float(v) for k, v in NUMBER.findall(text)}
            yield VideoWatchEvent(
                user_id=user, video_id=video, block_id=block, kind=KIND_REFUSAL,
                reason=reason, created_at=at,
                position_to=numbers.get("позиция"),
                watched_seconds=numbers.get("честных"),
                duration_seconds=numbers.get("длительность") or None,
            )


def _key(e: VideoWatchEvent):
    return (e.user_id, e.video_id, e.kind, e.reason, e.created_at.replace(microsecond=0).timestamp())


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="записать в базу")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        video_by_block = dict(
            db.query(TaskBlock.id, LearningVideo.bunny_video_id)
            .join(LearningVideo, LearningVideo.id == TaskBlock.video_id)
            .all()
        )
        events = list(parse(sys.stdin, video_by_block))
        if not events:
            print("В журнале нет строк незачёта.")
            return
        since = min(e.created_at for e in events)
        existing = {
            _key(e) for e in db.query(VideoWatchEvent).filter(VideoWatchEvent.created_at >= since)
        }
        fresh = [e for e in events if _key(e) not in existing]
        counts = collections.Counter((e.kind, e.reason) for e in fresh)
        print(f"Строк в журнале: {len(events)}, новых: {len(fresh)}, уже в базе: {len(events) - len(fresh)}")
        for (kind, reason), n in counts.most_common():
            print(f"  {kind} / {reason}: {n}")
        if not args.apply:
            print("Ничего не записано: запустите с --apply.")
            return
        db.add_all(fresh)
        db.commit()
        print(f"Записано: {len(fresh)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
