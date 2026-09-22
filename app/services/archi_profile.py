"""Architectural diagnostics, including legacy starter profile results."""

from itertools import product

from app.models.task_block import BLOCK_QUESTION, QUESTION_SINGLE

TITLE = "Диагностика АРХИ-ПРОФИЛЯ"
INTRO = (
    "Ответь на три вопроса и узнай свой архитектурный профиль на данный момент. "
    "Здесь нет правильных и неправильных ответов. Профиль может меняться "
    "с опытом и не определяет тебя навсегда."
)

QUESTIONS = (
    (
        "Ты обладаешь уникальной внутренней системой восприятия окружающего мира. Когда ты взаимодействуешь с ним, твой мозг запускает собственный процесс. Этот процесс похож на канал, по которому к тебе приходит информация. У каждого канал настроен по-своему. Посмотри на варианты и выбери наиболее подходящий тебе.",
        (
            "Я воспринимаю через ощущение. Когда я смотрю на архитектуру, я сначала чувствую её атмосферу, настроение, свет, тепло или тревогу. Мне важно, как это ощущается. Я люблю архитектуру, которая вызывает сильные эмоции, ВАУ-архитектуру, которая трогает.",
            "Я воспринимаю через смысл. Мне важно понять, что это значит и как это устроено. Я обычно погружаюсь глубоко в вопрос, чтобы разобраться в устройстве. В архитектуре я всегда замечаю детали, потому что ищу логику и значение. Мне интересны здания, в которых есть глубина и смысл.",
            "Я всегда ищу связи, когда изучаю что-то новое. Когда я смотрю на архитектуру, мне хочется понять, как одно связано с другим: как форма связана с функцией, как конструкция связана с материалом, как здание связано с местом. Я вижу не отдельные объекты, а отношения между ними. Мне интересно, как всё соотносится и складывается в единую картину.",
        ),
    ),
    (
        "Когда перед тобой стоит задача что-то сделать, как ты обычно начинаешь выполнять её?",
        (
            "Я проявляюсь через ощущение. Когда я начинаю создавать, я сначала чувствую: беру карандаш и начинаю наносить пятна, штрихи, искать настроение. Мне важно почувствовать материал и передать атмосферу. Я не строю план, как рисовать, я иду за рукой.",
            "Я проявляюсь через связи. Когда я начинаю создавать, я сначала думаю: разбираю задачу на части, ищу логику между частями, анализирую саму задачу и строю план выполнения. Мне важно понять, как это работает, прежде чем начать. Когда рисую, работаю чёткой линией, веду работу последовательно, шаг за шагом.",
            "Я проявляюсь через форму. Когда я начинаю что-то делать, я сначала вижу общую картину, как всё будет выглядеть целиком. Мне важно почувствовать целое, прежде чем начать. Я не люблю начинать с деталей: сначала представляю, как всё соединится вместе. Я ищу баланс и порядок.",
        ),
    ),
    (
        "Когда ты что-то создаёшь, какой результат ты обычно представляешь и что тебе хочется получить?",
        (
            "Я хочу, чтобы моя работа вызывала эмоцию у зрителя: грусть, радость, тревогу или восхищение. Важно, чтобы зритель не оставался равнодушен к тому, что я создаю.",
            "Мне важно, чтобы моя работа была обоснованной. Мне нравится логичность, ясность и точность, чтобы было ощущение, что в моей работе всё связано между собой, а человек, который на неё смотрит, не просто почувствовал, а разобрался, как она устроена, почему она такая и как решает поставленную задачу.",
            "Я стремлюсь к новизне. Мне важно, чтобы работа была новой, не такой, как раньше. Я хочу, чтобы человек, который смотрит на мою работу, увидел то, чего ещё не было. Не просто почувствовал или понял, а удивился: «Я такого ещё не видел». Важны новизна и целостность.",
        ),
    ),
)

OPTION_LABELS = (
    ("1. Через ощущение", "2. Через смысл", "3. Через связи"),
    ("1. Через ощущение", "2. Через связи", "3. Через форму"),
    ("1. Эмоция", "2. Обоснованность", "3. Новизна"),
)

PROFILES = {
    "artist": ("Архитектор-художник", "Эмоция + атмосфера", "Я создаю настроение, моя сила в передаче уникального чувства.", "Антонио Гауди, Фрэнк Гери, Томас Хизервик"),
    "constructor": ("Архитектор-конструктор", "Логика + структура", "Я создаю ясность, моя сила в чёткой организации объекта.", "Константин Мельников, Норман Фостер, Моисей Гинзбург"),
    "analyst": ("Архитектор-аналитик", "Анализ + точность", "Я понимаю каждый нюанс, моя сила в глубине и передаче смыслов.", "Сантьяго Калатрава, Питер Айзенман, Бернар Чуми"),
    "provocateur": ("Архитектор-провокатор", "Смелость + выразительность", "Я выхожу за рамки, моя сила в умении ответить на вызов и не бояться делать несообразно.", "Рем Колхас, Владимир Татлин, Владимир Шухов"),
    "innovator": ("Архитектор-новатор", "Форма + новизна", "Я создаю новое, моя сила в оригинальности и разрыве шаблона.", "Заха Хадид, Эль Лисицкий, Яков Чернихов"),
    "synthetic": ("Архитектор-синтетик", "Гибкость + синтез", "Я создаю целое из разного, моя сила в адаптивности и уникальных комбинациях.", "Алвар Аалто, Ван Шу, Тадао Андо, Алексей Щусев, Кенго Кума, Оскар Нимейр"),
}

COMBINATIONS = {
    **dict.fromkeys(("111", "112", "113"), "artist"),
    **dict.fromkeys(("221", "222", "223"), "constructor"),
    **dict.fromkeys(("231", "232", "233", "321", "322", "323", "213"), "analyst"),
    **dict.fromkeys(("131", "132", "133", "311", "312", "313", "123"), "provocateur"),
    **dict.fromkeys(("331", "332", "333"), "innovator"),
    **dict.fromkeys(("121", "122", "211", "212"), "synthetic"),
}


def preset_blocks() -> list[dict]:
    return [
        {
            "block_type": BLOCK_QUESTION,
            "title": f"Вопрос {index}",
            "body": body,
            "question_type": QUESTION_SINGLE,
            "is_required": index == 3,
            "options": [
                {"text": OPTION_LABELS[index - 1][number - 1], "description": option, "is_correct": False}
                for number, option in enumerate(options, 1)
            ],
        }
        for index, (body, options) in enumerate(QUESTIONS, 1)
    ]


def validate_diagnostic_config(raw: dict) -> dict:
    """Normalize a complete teacher-authored diagnostic before publishing."""
    if not isinstance(raw, dict):
        raise ValueError("Добавьте вопросы и результаты диагностики")
    questions = raw.get("questions")
    results = raw.get("results")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 50:
        raise ValueError("Добавьте от одного до 50 вопросов")
    clean_questions = []
    # Номер вопроса и варианта — в каждое сообщение (владелец 22.09.2026,
    # запрос после того, как общее «укажите текст и значение» не давало
    # понять, какой именно из вопросов не сохраняется). Условия разнесены по
    # одному: раньше один `raise` на пять причин через `or` называл только
    # самый общий симптом, а не то, что реально сломано.
    for qi, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            raise ValueError(f"Вопрос {qi}: заполните текст вопроса")
        prompt = str(question.get("text") or "").strip()
        options = question.get("options")
        if not prompt:
            raise ValueError(f"Вопрос {qi}: не заполнен текст вопроса")
        if len(prompt) > 5000:
            raise ValueError(f"Вопрос {qi}: текст вопроса длиннее 5000 символов")
        if not isinstance(options, list) or not 2 <= len(options) <= 20:
            raise ValueError(f"Вопрос {qi}: нужно от двух до 20 вариантов ответа")
        clean_options = []
        values = set()
        for oi, option in enumerate(options, 1):
            if not isinstance(option, dict):
                raise ValueError(f"Вопрос {qi}, вариант {oi}: заполните вариант ответа")
            text = str(option.get("text") or "").strip()
            value = str(option.get("value") or "").strip()
            if not text:
                raise ValueError(f"Вопрос {qi}, вариант {oi}: не заполнен текст ответа")
            # Без верхней границы (владелец 22.09.2026): у встроенной
            # диагностики варианты ответа — целые абзацы длиннее 300
            # символов (см. QUESTIONS выше), они хранятся в обход этой
            # проверки — значит планка была лишней и для teacher-authored.
            if not value:
                raise ValueError(f"Вопрос {qi}, вариант {oi}: не заполнено значение")
            if len(value) > 20:
                raise ValueError(f"Вопрос {qi}, вариант {oi}: значение длиннее 20 символов")
            if not value.isalnum():
                raise ValueError(
                    f"Вопрос {qi}, вариант {oi}: значение «{value}» содержит недопустимый "
                    "символ — можно только буквы и цифры, без пробелов и знаков"
                )
            if value in values:
                raise ValueError(f"Вопрос {qi}: значение «{value}» повторяется у двух вариантов")
            values.add(value)
            clean_options.append({"text": text, "value": value})
        clean_questions.append({"text": prompt, "options": clean_options})
    combinations = list(product(*[[option["value"] for option in q["options"]] for q in clean_questions]))
    if len(combinations) > 4096:
        raise ValueError("Слишком много сочетаний: сократите число вопросов или вариантов")
    expected = set(combinations)
    used = set()
    if not isinstance(results, list) or not results:
        raise ValueError("Добавьте хотя бы один результат")
    clean_results = []
    for ri, result in enumerate(results, 1):
        if not isinstance(result, dict):
            raise ValueError(f"Результат {ri}: заполните результат")
        title = str(result.get("title") or "").strip()
        body = str(result.get("text") or "").strip()
        # Необязательное поле (владелец 22.09.2026): у части диагностик нет
        # реальных архитекторов-примеров, карточка результата должна
        # сохраняться и без него.
        architects = str(result.get("architects") or "").strip()
        assigned = result.get("combinations")
        if not title:
            raise ValueError(f"Результат {ri}: не заполнен архитектурный профиль (название)")
        if len(title) > 200:
            raise ValueError(f"Результат {ri}: архитектурный профиль длиннее 200 символов")
        if not body:
            raise ValueError(f"Результат {ri} («{title}»): не заполнена формула силы")
        if len(body) > 5000:
            raise ValueError(f"Результат {ri} («{title}»): формула силы длиннее 5000 символов")
        if len(architects) > 500:
            raise ValueError(f"Результат {ri} («{title}»): список реальных архитекторов слишком длинный")
        if not isinstance(assigned, list) or not assigned:
            raise ValueError(f"Результат {ri} («{title}»): назначьте хотя бы одно сочетание ответов")
        clean_assigned = []
        for combination in assigned:
            if not isinstance(combination, list):
                raise ValueError(f"Результат {ri} («{title}»): неверное сочетание ответов")
            key = tuple(str(value) for value in combination)
            label = " · ".join(key)
            if key not in expected:
                raise ValueError(f"Результат {ri} («{title}»): сочетания {label} нет среди вариантов ответов")
            if key in used:
                raise ValueError(f"Сочетание {label} назначено сразу двум результатам")
            used.add(key)
            clean_assigned.append(list(key))
        clean_results.append({"title": title, "text": body, "architects": architects, "combinations": clean_assigned})
    if used != expected:
        raise ValueError(f"Назначьте результат каждому сочетанию ответов: осталось {len(expected - used)}")
    return {"questions": clean_questions, "results": clean_results}


def blocks_from_config(config: dict) -> list[dict]:
    return [
        {"block_type": BLOCK_QUESTION, "title": f"Вопрос {index}", "body": question["text"],
         "question_type": QUESTION_SINGLE, "is_required": index == len(config["questions"]),
         "options": [{"text": option["text"], "is_correct": False} for option in question["options"]]}
        for index, question in enumerate(config["questions"], 1)
    ]


def result_for_answers(db, task_id: int, user_id: int) -> dict | None:
    from app.models.task_block import TaskBlockResponse
    from app.services.task_blocks import get_blocks, get_options, get_selected_options

    from app.models.tracker import TrackerTask

    task = db.get(TrackerTask, task_id)
    config = task.diagnostic_config if task else None
    blocks = [b for b in get_blocks(db, task_id) if b.block_type == BLOCK_QUESTION]
    if len(blocks) != (len(config["questions"]) if config else 3):
        return None
    response = db.query(TaskBlockResponse).filter_by(task_id=task_id, user_id=user_id).one_or_none()
    if response is None:
        return None
    selected = get_selected_options(db, response_id=response.id)
    options = get_options(db, [block.id for block in blocks])
    digits = []
    for block in blocks:
        chosen = selected.get(block.id, set())
        if len(chosen) != 1:
            return None
        option_id = next(iter(chosen))
        position = next((i for i, option in enumerate(options.get(block.id, []), 1) if option.id == option_id), None)
        if position is None:
            return None
        digits.append(config["questions"][len(digits)]["options"][position - 1]["value"] if config else str(position))
    if config:
        key = tuple(digits)
        result = next((r for r in config["results"] if list(key) in r["combinations"]), None)
        if result is None:
            return None
        combination = "".join(digits) if all(len(value) == 1 for value in digits) else " · ".join(digits)
        return {
            "combination": combination, "title": result["title"], "traits": "",
            "formula": result["text"], "architects": result.get("architects", ""),
        }
    combination = "".join(digits)
    key = COMBINATIONS.get(combination)
    if key is None:
        return None
    title, traits, formula, architects = PROFILES[key]
    return {"combination": combination, "title": title, "traits": traits, "formula": formula, "architects": architects}
