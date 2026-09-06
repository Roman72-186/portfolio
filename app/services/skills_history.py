"""История самооценки навыков ученика (владелец 03.09.2026).

«Нужно как-то сделать, чтобы вот эта информация погружалась ему в его личную
вкладку… в начале обучения было так, в середине уже вот так, а к концу ещё
более развито. Было бы круто просто наложить друг на друга и посмотреть рост.»

Отдельной таблицы под это нет: диагностика — блок `scale` универсального
конструктора, навыки лежат вариантами блока, оценка — текстом выбранного
варианта. Здесь только чтение и сборка динамики по датам.
"""
from sqlalchemy.orm import Session

from app.models.task_block import (
    BLOCK_SCALE,
    TaskBlock,
    TaskBlockAnswer,
    TaskBlockAnswerOption,
    TaskBlockOption,
    TaskBlockResponse,
)


def skills_history(db: Session, user_id: int) -> list[dict]:
    """Оценки навыков ученика по датам, от ранних к поздним.

    Возвращает `[{"skill": str, "points": [{"date": date, "score": int}]}]` —
    по строке на навык, чтобы шкалы можно было наложить друг на друга. Навык
    опознаётся по названию варианта, а не по его id: диагностику в разные
    периоды заводят разными блоками, но «Стрессоустойчивость» в них одна и та
    же.
    """
    rows = (
        db.query(
            TaskBlockOption.text,
            TaskBlockAnswerOption.text,
            TaskBlockResponse.updated_at,
        )
        .join(TaskBlockAnswerOption, TaskBlockAnswerOption.option_id == TaskBlockOption.id)
        .join(TaskBlockAnswer, TaskBlockAnswer.id == TaskBlockAnswerOption.answer_id)
        .join(TaskBlockResponse, TaskBlockResponse.id == TaskBlockAnswer.response_id)
        .join(TaskBlock, TaskBlock.id == TaskBlockAnswer.block_id)
        .filter(
            TaskBlockResponse.user_id == user_id,
            TaskBlock.block_type == BLOCK_SCALE,
        )
        .order_by(TaskBlockResponse.updated_at)
        .all()
    )

    by_skill: dict[str, list[dict]] = {}
    for skill, score_text, answered_at in rows:
        try:
            score = int((score_text or "").strip())
        except ValueError:
            # Оценка не числом — данные из старого ответа или ручной правки.
            # Пропускаем молча: график важнее одной битой точки.
            continue
        by_skill.setdefault(skill, []).append(
            {"date": answered_at, "score": score}
        )
    return [
        {"skill": skill, "points": points}
        for skill, points in sorted(by_skill.items())
    ]
