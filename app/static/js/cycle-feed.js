(function () {
    // Ключ — атрибутом тега в `partials/inline/cycle_feed.html`: файл кэшируется,
    // а ключ у каждой сессии свой.
    var script = document.currentScript;
    var csrfToken = script ? script.getAttribute('data-csrf-token') : '';
    var feed = document.querySelector('.lrn-feed');
    if (!feed) return;

    var el = window.lrnBlockRender.el;
    var steps = Array.prototype.filter.call(feed.querySelectorAll('[data-block-id]'), function (step) {
        return step.querySelector('[data-role="block-body"]');
    });

    // Один запрос на задание, а не на блок: у задания из пяти блоков это
    // пять одинаковых ответов сервера вместо одного.
    var taskSteps = new Map();
    steps.forEach(function (step) {
        var id = step.getAttribute('data-task-id');
        if (!taskSteps.has(id)) taskSteps.set(id, []);
        taskSteps.get(id).push(step);
    });

    function stepFor(taskId, blockId) {
        return feed.querySelector(
            '[data-task-id="' + taskId + '"][data-block-id="' + blockId + '"]'
        );
    }

    function fail(status, text) {
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

    function refreshSteps(hint, refreshIds) {
        var refresh = new Set((refreshIds || []).map(String));
        hint.hidden = false;
        hint.classList.remove('is-error');
        hint.textContent = 'Обновляем…';
        window.fetchWithTimeout(window.location.href, {
            credentials: 'same-origin',
            cache: 'no-store'
        }).then(function (resp) {
            if (!resp.ok) throw new Error();
            return resp.text();
        }).then(function (html) {
            var page = new DOMParser().parseFromString(html, 'text/html');
            var updated = page.querySelector('.lrn-feed');
            if (!updated) throw new Error();

            ['[data-learning-cta]', '[data-student-progress]'].forEach(function (selector) {
                var current = document.querySelector(selector);
                var fresh = page.querySelector(selector);
                if (current && fresh && current.innerHTML !== fresh.innerHTML) {
                    current.replaceChildren(...fresh.childNodes);
                }
            });

            var changed = new Map();
            updated.querySelectorAll('.lrn-step').forEach(function (fresh) {
                var taskId = fresh.dataset.taskId;
                var blockId = fresh.dataset.blockId;
                var current = blockId
                    ? stepFor(taskId, blockId)
                    : feed.querySelector('.lrn-step[data-task-id="' + taskId + '"]:not([data-block-id])');
                if (!current) return;
                var unlocked = current.classList.contains('lrn-step--locked')
                    && !fresh.classList.contains('lrn-step--locked');
                if (unlocked) {
                    fresh.toggleAttribute('data-subject-off', current.hasAttribute('data-subject-off'));
                    current.replaceWith(fresh);
                    current = fresh;
                } else if (fresh.classList.contains('lrn-step--done')
                    && current.classList.contains('lrn-step--current')) {
                    // Тело карточки остаётся на месте: плеер и формы сохраняют состояние.
                    current.classList.remove('lrn-step--current');
                    current.classList.add('lrn-step--done', 'is-open');
                    current.querySelector('.lrn-step-head').replaceWith(fresh.querySelector('.lrn-step-head'));
                    var toggle = current.querySelector('.lrn-step-toggle');
                    if (toggle) window.lrnToggleStep(toggle, true);
                }

                if (!unlocked) {
                    var oldLock = current.querySelector('.lrn-step-locked');
                    var newLock = fresh.querySelector('.lrn-step-locked');
                    if (oldLock && newLock) oldLock.textContent = newLock.textContent;
                    var oldButton = current.querySelector('[data-toggle-task]');
                    var newButton = fresh.querySelector('[data-toggle-task]');
                    if (oldButton && newButton && (oldButton.disabled !== newButton.disabled
                        || oldButton.getAttribute('aria-describedby') !== newButton.getAttribute('aria-describedby')
                        || oldButton.getAttribute('aria-pressed') !== newButton.getAttribute('aria-pressed'))) {
                        oldButton.parentNode.replaceChildren(...newButton.parentNode.childNodes);
                    }
                }
                if ((unlocked || refresh.has(blockId)) && current.querySelector('[data-role="block-body"]')) {
                    if (!changed.has(taskId)) changed.set(taskId, []);
                    changed.get(taskId).push(current);
                }
            });
            changed.forEach(function (cards, taskId) { loadTask(taskId, cards, true); });
            hint.hidden = true;
            hint.textContent = '';
        }).catch(function () {
            hint.hidden = false;
            hint.classList.add('is-error');
            hint.textContent = 'Изменение сохранено, но ленту не удалось обновить. ';
            var retry = el('button', 'btn-outline', 'Повторить');
            retry.type = 'button';
            retry.addEventListener('click', function () {
                retry.disabled = true;
                refreshSteps(hint, refreshIds);
            });
            hint.appendChild(retry);
        });
    }

    feed.addEventListener('lrn-task-saved', function (event) {
        var hint = event.detail && event.detail.hint;
        if (!hint) hint = event.target.appendChild(el('p', 'video-progress-status'));
        refreshSteps(hint);
    });

    function renderTask(taskId, data, onlyCards) {
        if (onlyCards) onlyCards.forEach(function (card) { card.hidden = false; });
        function renderableStep(blockId) {
            var step = stepFor(taskId, blockId);
            return !onlyCards || onlyCards.indexOf(step) !== -1 ? step : null;
        }
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
            onBlockSaved: function (block, hint, reloadBlock) {
                refreshSteps(hint, reloadBlock ? [block.id] : []);
            }
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
                var step = renderableStep(block.id);
                var body = step && step.querySelector('[data-role="block-body"]');
                if (!step || !body) return false;
                wizardSteps.push({ block: block, container: step, body: body });
                return true;
            });
            if (complete) {
                window.lrnBlockRender.runWizard(renderer, wizardSteps, {
                    onFinish: function (answers, handlers) {
                        postAnswers(data.submit_endpoint, answers).then(function () {
                            refreshSteps(wizardSteps[0].body.appendChild(el('p', 'video-progress-status')),
                                diagnosticBlocks.map(function (block) { return block.id; }));
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
            var cards = poll.blocks.map(function (block) { return renderableStep(block.id); });
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
                        refreshSteps(host.appendChild(el('p', 'video-progress-status')),
                            poll.blocks.map(function (block) { return block.id; }));
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
            var step = renderableStep(block.id);
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
        var formBlocks = (data.blocks || []).filter(function (block) {
            if (wizardIds[block.id]) return false;
            var step = stepFor(taskId, block.id);
            // Будущие шаги есть в JSON, но их полей ещё нет на экране.
            // Они не должны включать пустую форму под проверенным опросом.
            if (!step || !step.querySelector('[data-role="block-body"]')) return false;
            // У шкалы своя кнопка; неотвеченная шкала опроса сохраняется
            // мастером или общей формой, если мастер ещё не запущен.
            if (block.block_type === 'scale' && block.submit_endpoint
                && !(block.poll_key && !block.answered)) return false;
            return true;
        });
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
                var edited = formBlocks.filter(function (block) {
                    return answers.some(function (answer) { return String(answer.block_id) === String(block.id); });
                }).map(function (block) { return block.id; });
                edited.push(host.closest('[data-block-id]').dataset.blockId);
                refreshSteps(note, edited);
            }).catch(function () {
                button.disabled = false;
                note.textContent = 'Не удалось отправить ответ. Попробуй ещё раз.';
                note.classList.add('is-error');
            });
        });
        host.appendChild(form);
    }

    function loadTask(taskId, cards, onlyNewCards) {
        cards.forEach(function (card) {
            var status = card.querySelector('[data-role="block-body"] > [data-role="status"]');
            if (!status) return;
            var retry = status.parentNode.querySelector('[data-block-retry]');
            if (retry) retry.remove();
            status.textContent = 'Загружаем…';
            status.classList.remove('is-error');
        });
        window.fetchWithTimeout('/cabinet/tracker/tasks/' + taskId + '/blocks', {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function (resp) {
            if (!resp.ok) throw new Error();
            return resp.json();
        }).then(function (data) {
            if (!data || !Array.isArray(data.blocks)) throw new Error();
            return data;
        }).then(function (data) {
            renderTask(taskId, data, onlyNewCards ? cards : null);
            var ids = new Set(data.blocks.map(function (block) { return String(block.id); }));
            cards.forEach(function (card) {
                if (ids.has(card.dataset.blockId)) return;
                var status = card.querySelector('[data-role="block-body"] > [data-role="status"]');
                if (status) fail(status, 'Этот блок недоступен. Обнови страницу, чтобы увидеть актуальное задание.');
            });
        }).catch(function () {
            showLoadError(taskId, cards, onlyNewCards);
        });
    }

    function showLoadError(taskId, cards, onlyNewCards) {
        cards.forEach(function (card) {
            var body = card.querySelector('[data-role="block-body"]');
            var status = body && body.querySelector(':scope > [data-role="status"]');
            if (!status && body && onlyNewCards) {
                status = body.appendChild(el('p', 'video-progress-status'));
                status.setAttribute('data-role', 'status');
                status.setAttribute('aria-live', 'polite');
            }
            if (!status) return;
            var previousRetry = status.parentNode.querySelector('[data-block-retry]');
            if (previousRetry) previousRetry.remove();
            fail(status, 'Не удалось загрузить содержимое.');
            var retry = el('button', 'btn-outline', 'Повторить');
            retry.type = 'button';
            retry.setAttribute('data-block-retry', '');
            retry.addEventListener('click', function () { loadTask(taskId, cards, onlyNewCards); });
            status.after(retry);
        });
    }

    taskSteps.forEach(function (cards, taskId) { loadTask(taskId, cards); });
})();
