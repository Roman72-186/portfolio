/*
Тренажёр диагностики для ГП/СА (владелец 22.09.2026, вариант C развилки —
см. `NEXT-CHAT-PROMPT-ДИАГНОСТИКА-ДОСТУП.md`): реально ответить на вопросы
и увидеть результат, ничего не сохраняя. Один модуль на оба конструктора
(дня и заданий цикла) — правка одного экрана без другого уже случалась
(прецедент 09.09.2026, `AGENTS.md`).

Переиспользует существующий рендерер блоков ученика (`task-blocks-render.js`:
`runWizard`, `profileResult`) — тот же, что рисует настоящее прохождение
диагностики. Доступен только для уже сохранённой диагностики: вопросам и
вариантам нужны настоящие id из базы (`/cabinet/staff/program/tasks/{id}/
trainer-blocks`), у несохранённого черновика их нет — там по-прежнему только
обычный предпросмотр «Глазами ученика».

Сервер (`app/api/cabinet_program.py`) ничего не пишет в БД — ни
`TaskBlockResponse`, ни состояние блока: результат считает и сразу забывает.
*/
(function () {
    // Читаем тело ответа текстом и разбираем сами (не `resp.json()` напрямую):
    // на невалидном JSON `resp.json()` бросает исключение с формулировкой
    // браузера — в Safari это нечитаемое «The string did not match the
    // expected pattern.» без кода ответа и без куска тела, по которому можно
    // понять причину. Здесь вместо неё — свой текст с кодом ответа и началом
    // тела, чтобы следующий такой случай был виден сразу, а не гадался заново.
    function readJson(resp) {
        return resp.text().then(function (raw) {
            var body = null;
            try { body = raw ? JSON.parse(raw) : null; } catch (e) { body = null; }
            if (body === null) {
                throw new Error(
                    'Сервер ответил неожиданно (код ' + resp.status + '). '
                    + (raw ? raw.slice(0, 200) : 'Пустой ответ.')
                );
            }
            return { ok: resp.ok, status: resp.status, body: body };
        });
    }

    window.diagnosticTrainer = {
        run: function (options) {
            var box = options.box;
            var taskId = options.taskId;
            var csrfToken = options.csrfToken;
            var el = window.lrnBlockRender.el;

            box.innerHTML = '';
            var status = el('p', 'video-progress-status', 'Загружаем вопросы…');
            status.setAttribute('aria-live', 'polite');
            box.appendChild(status);
            box.hidden = false;

            fetch('/cabinet/staff/program/tasks/' + taskId + '/trainer-blocks', {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' }
            }).then(readJson).then(function (result) {
                if (!result.ok) throw new Error(result.body && result.body.detail);
                var blocks = result.body.blocks || [];
                if (!blocks.length) throw new Error('У диагностики нет вопросов.');

                box.innerHTML = '';
                var list = el('div', 'lrn-blk-list');
                box.appendChild(list);

                var renderer = window.lrnBlockRender.create({ csrfToken: csrfToken, answered: false });
                var steps = blocks.map(function (block) {
                    var stepBody = el('div', 'lrn-blk-step');
                    list.appendChild(stepBody);
                    return { block: block, container: stepBody, body: stepBody };
                });

                window.lrnBlockRender.runWizard(renderer, steps, {
                    onFinish: function (answers, handlers) {
                        fetch('/cabinet/staff/program/tasks/' + taskId + '/trainer-score', {
                            method: 'POST',
                            credentials: 'same-origin',
                            headers: {
                                'Accept': 'application/json',
                                'Content-Type': 'application/json',
                                'X-CSRF-Token': csrfToken
                            },
                            body: JSON.stringify({ answers: answers })
                        }).then(readJson).then(function (result) {
                            if (!result.ok || !result.body.ok) {
                                throw new Error(result.body && result.body.error);
                            }
                            list.hidden = true;
                            box.appendChild(window.lrnBlockRender.profileResult(result.body.archi_profile));
                        }).catch(function (err) {
                            handlers.onError();
                            if (err && err.message) {
                                var note = box.querySelector('.video-progress-status.is-error');
                                if (note) note.textContent = err.message;
                            }
                        });
                    }
                });
            }).catch(function (err) {
                box.innerHTML = '';
                box.appendChild(el(
                    'p', 'video-progress-status is-error',
                    (err && err.message) || 'Не удалось загрузить вопросы диагностики.'
                ));
                box.hidden = false;
            });
        }
    };
})();
