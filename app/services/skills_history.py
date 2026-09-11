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
    SCALE_MAX,
    SCALE_MIN,
    TaskBlock,
    TaskBlockAnswer,
    TaskBlockAnswerOption,
    TaskBlockOption,
    TaskBlockResponse,
)


def skills_history(db: Session, user_id: int) -> list[dict]:
    """Оценки навыков ученика по датам, от ранних к поздним.

    Возвращает `[{"skill": str, "description": str|None,
    "points": [{"date": date, "score": int, "percent": int}]}]` — по строке
    на навык, чтобы шкалы можно было наложить друг на друга. `percent` —
    положение точки на шкале 0-100%, посчитано здесь, а не в шаблоне: Jinja
    не должен знать про SCALE_MIN/SCALE_MAX константы модели. Навык
    опознаётся по названию варианта, а не по его id: диагностику в разные
    периоды заводят разными блоками, но «Стрессоустойчивость» в них одна и та
    же.

    Сравнение названия — без учёта регистра и лишних пробелов (владелец
    11.09.2026): куратор набирает название заново в каждой новой волне
    диагностики (см. `docstring` модуля — каждый период обязан быть новым
    блоком), и опечатка вида «стрессоустойчивость» вместо
    «Стрессоустойчивость» не должна тихо завести вторую строку в профиле.
    Показываемое название и описание берутся из **последнего** по дате
    ответа — так профиль не застревает на формулировке из первой волны.
    """
    rows = (
        db.query(
            TaskBlockOption.text,
            TaskBlockOption.description,
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

    by_skill: dict[str, dict] = {}
    for skill_text, description, score_text, answered_at in rows:
        try:
            score = int((score_text or "").strip())
        except ValueError:
            # Оценка не числом — данные из старого ответа или ручной правки.
            # Пропускаем молча: график важнее одной битой точки.
            continue
        key = (skill_text or "").strip().lower()
        if not key:
            continue
        entry = by_skill.setdefault(key, {"skill": skill_text.strip(), "description": None, "points": []})
        entry["skill"] = skill_text.strip()
        entry["description"] = description
        span = SCALE_MAX - SCALE_MIN or 1
        percent = round((score - SCALE_MIN) / span * 100)
        entry["points"].append({
            "date": answered_at,
            "score": score,
            "percent": max(0, min(100, percent)),
        })
    return sorted(by_skill.values(), key=lambda row: row["skill"])
