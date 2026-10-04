(function () {
    // Ключ — атрибутом тега в `partials/inline/cycle_feed.html`: файл кэшируется,
    // а ключ у каждой сессии свой.
    var script = document.currentScript;
    var csrfToken = script ? script.getAttribute('data-csrf-token') : '';
    var feed = document.querySelector('.lrn-feed');
    if (!feed) return;

    var el = window.lrnBlockRender.el;
    var steps = Array.prototype.slice.call(feed.querySelectorAll('[data-block-id]'));
    if (!steps.length) return;

    // Один запрос на задание, а не на блок: у задания из пяти блоков это
    // пять одинаковых ответов сервера вместо одного.
    var taskIds = [];
    steps.forEach(function (step) {
        var id = step.getAttribute('data-task-id');
        if (taskIds.indexOf(id) === -1) taskIds.push(id);
    });

    function stepFor(taskId, blockId) {
        return feed.querySelector(
            '[data-task-id="' + taskId + '"][data-block-id="' + blockId + '"]'
        );
    }

    function fail(step, text) {
        var status = step.querySelector('[data-role="status"]');
        if (!status) return;
        status.textContent = text;
        status.classList.add('is-error');
    }

    function postAnswers(endpoint, answers) {
        return fetch(endpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken
            },
            body: JSON.stringify({ answers: answers })
        }).then(function (resp) {
            if (!resp.ok) throw new Error();
            return resp.json();
        });
    }

    // Галочки правил сохранились сами (владелец 04.10.2026). Статусы шагов
    // считает сервер, поэтому лента перезагружается, а метка `after` просит
    // показать шаг, открывшийся ниже сохранённого (`lrnFocusAfterStep` в
    // `cabinet_learning.html`). `task` снимаем: фокус ссылки из трекера
    // раскрыл бы уже сделанный шаг. `replace` — новая загрузка без
    // восстановления прокрутки, которое вернуло бы ученика к правилам.
    function showStepAfter(block) {
        var url = new URL(window.location.href);
        url.searchParams.delete('task');
        url.searchParams.set('after', block.id);
        url.hash = '';
        window.location.replace(url.toString());
    }

    function renderTask(taskId, data) {
        // `titlesOutside` — подпись шага печатает сервер
        // (`cabinet_learning.html`, `<h2 class="lrn-step-title">`), поэтому
        // внутри блока она не нужна: до 16.09.2026 ученик читал её дважды
        // подряд. Флаг только здесь — у панели «Материалы задания»
        // (`task_blocks.html`) и у обоих предпросмотров преподавателя
        // внутренний заголовок единственная подпись блока.
        var renderer = window.lrnBlockRender.create({
            csrfToken: csrfToken,
            answered: !!data.answered,
            titlesOutside: true,
            onRulesSaved: showStepAfter
        });
        var blocks = data.blocks || [];

        // Диагностика без ответа — мастер «один вопрос за раз» (ТЗ
        // «Диагностика», раздел 8): каждый вопрос уже своя карточка шага
        // ленты (`data-block-id`), мастер прячет все, кроме текущей. Если
        // хотя бы одна карточка ещё не пришла на экран (заперта, к примеру),
        // откатываемся к обычному показу всех вопросов разом ниже.
        //
        // Мастер берёт только вопросы диагностики (`block.is_archi_profile`),
        // а не весь `blocks` — с 24.09.2026 диагностика может быть встроена
        // в «Задание» рядом с обычными блоками (видео, фото), и `data.blocks`
        // несёт их все вперемешку. Взять весь список означало бы завести в
        // мастер и чужие блоки — `runWizard` прячет всё, кроме текущего шага
        // (`container.hidden`), и видео с фото-загрузкой пропадали бы из
        // ленты вместе с недостигнутыми вопросами диагностики.
        if (data.is_archi_profile && !data.archi_profile && !data.answered) {
            var diagnosticBlocks = blocks.filter(function (block) { return block.is_archi_profile; });
            var wizardSteps = [];
            var complete = diagnosticBlocks.length > 0 && diagnosticBlocks.every(function (block) {
                var step = stepFor(taskId, block.id);
                var body = step && step.querySelector('[data-role="block-body"]');
                if (!step || !body) return false;
                wizardSteps.push({ block: block, container: step, body: body });
                return true;
            });
            if (complete) {
                window.lrnBlockRender.runWizard(renderer, wizardSteps, {
                    onFinish: function (answers, handlers) {
                        postAnswers(data.submit_endpoint, answers).then(function () {
                            window.location.reload();
                        }).catch(function () { handlers.onError(); });
                    }
                });
                // Остальные блоки задания (не диагностика) идут обычным
                // рендером ниже — мастер занял только свои карточки шагов.
                blocks = blocks.filter(function (block) { return !block.is_archi_profile; });
            }
        }

        var lastQuestionBody = null;

        // Опрос (владелец 30.09.2026) — одна карточка ленты: сервер рисует
        // по карточке на вопрос, здесь все вопросы опроса уходят в первую,
        // остальные прячутся, а мастер листает вопросы внутри неё. Если
        // какой-то карточки нет на экране (заперта), опрос рисуется
        // по-старому, по карточке на вопрос. Блоки опроса с запущенным
        // мастером в общую форму ответов не идут: они уходят своим запросом.
        var wizardIds = {};
        window.lrnBlockRender.pollGroups(blocks).forEach(function (poll) {
            var cards = poll.blocks.map(function (block) { return stepFor(taskId, block.id); });
            var ready = cards.every(function (card) {
                return card && card.querySelector('[data-role="block-body"]');
            });
            if (!ready) return;
            var host = cards[0].querySelector('[data-role="block-body"]');
            host.innerHTML = '';
            cards.slice(1).forEach(function (card) {
                // Кнопка закрытия задания стоит под последним блоком
                // задания — если это вопрос опроса, переносим её в карточку
                // опроса, иначе она спряталась бы вместе с карточкой.
                var completion = card.querySelector('.lrn-task-completion');
                if (completion) cards[0].appendChild(completion);
                card.hidden = true;
            });
            var running = window.lrnBlockRender.runPoll(renderer, poll.blocks, host, {
                showTitle: false,
                onSubmit: function (answers, handlers) {
                    postAnswers(data.submit_endpoint, answers).then(function () {
                        window.location.reload();
                    }).catch(function () { handlers.onError(); });
                }
            });
            poll.blocks.forEach(function (block) {
                if (running) wizardIds[block.id] = true;
            });
            lastQuestionBody = host;
            blocks = blocks.filter(function (block) { return poll.blocks.indexOf(block) === -1; });
        });

        blocks.forEach(function (block, index) {
            var step = stepFor(taskId, block.id);
            // Блока может не быть на экране: запертый шаг содержимого не
            // получает вовсе — иначе «закрыто» обходилось бы чтением ответа
            // сервера в консоли браузера.
            if (!step) return;
            var body = step.querySelector('[data-role="block-body"]');
            if (!body) return;
            var node = renderer.render(block, index);
            body.innerHTML = '';
            if (node) body.appendChild(node);
            if (block.block_type === 'question') lastQuestionBody = body;
        });

        if (data.answered && data.gradable_count && lastQuestionBody) {
            lastQuestionBody.appendChild(el(
                'p', 'lrn-blk-score',
                'Верных ответов: ' + data.correct_count + ' из ' + data.gradable_count
            ));
        }
        if (data.archi_profile && lastQuestionBody) {
            lastQuestionBody.appendChild(window.lrnBlockRender.profileResult(data.archi_profile));
        }
        var formBlocks = (data.blocks || []).filter(function (block) { return !wizardIds[block.id]; });
        var formOpen = window.lrnBlockRender.formOpen(formBlocks);
        if (data.has_questions && data.submit_endpoint && lastQuestionBody && formOpen) {
            appendSubmit(taskId, data, renderer, lastQuestionBody, formBlocks);
        } else if (data.answered && data.has_questions && lastQuestionBody) {
            lastQuestionBody.appendChild(el('p', 'video-help', 'Ответ сохранён, изменить его нельзя.'));
        }
    }

    function appendSubmit(taskId, data, renderer, host, formBlocks) {
        var form = el('form', 'lrn-blk-submit');
        form.appendChild(el('p', 'lrn-blk-title', data.is_archi_profile
            ? 'Выбери по одному варианту в каждом вопросе. Здесь нет правильных ответов.'
            : 'Ответы можно менять до проверки и срока сдачи.'));
        var button = el('button', 'btn-blue', data.is_archi_profile
            ? 'Отправить'
            : 'Сохранить ответы');
        button.type = 'submit';
        var note = el('p', 'video-progress-status');
        note.setAttribute('aria-live', 'polite');
        form.appendChild(button);
        form.appendChild(note);
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var answers = renderer.collectAnswers(formBlocks, feed);
            if (data.is_archi_profile && (answers.length !== data.questions_left_count || answers.some(function (answer) { return answer.option_ids.length !== 1; }))) {
                note.textContent = 'Выбери по одному варианту в каждом вопросе.';
                note.classList.add('is-error');
                return;
            }
            note.textContent = '';
            note.classList.remove('is-error');
            button.disabled = true;
            // Ответы собираются со всей ленты: вопросы одного задания
            // разъехались по разным карточкам шагов.
            postAnswers(data.submit_endpoint, answers).then(function () {
                // Попытка одна, и статусы шагов считает сервер: после ответа
                // перезагружаем экран, чтобы вердикт, счёт и открывшиеся
                // ниже шаги пришли из одного источника.
                window.location.reload();
            }).catch(function () {
                button.disabled = false;
                note.textContent = 'Не удалось отправить ответ. Попробуй ещё раз.';
                note.classList.add('is-error');
            });
        });
        host.appendChild(form);
    }

    taskIds.forEach(function (taskId) {
        fetch('/cabinet/tracker/tasks/' + taskId + '/blocks', {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function (resp) {
            if (!resp.ok) throw new Error();
            return resp.json();
        }).then(function (data) {
            renderTask(taskId, data);
        }).catch(function () {
            feed.querySelectorAll('[data-task-id="' + taskId + '"]').forEach(function (step) {
                fail(step, 'Не удалось загрузить содержимое. Попробуй позже.');
            });
        });
    });
})();
