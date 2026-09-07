/*
Рендер блоков универсального конструктора — один на все экраны ученика.

Владелец 06.09.2026: восемь вкладок недели заменяются лентой блоков, и блок
теперь рисуется в двух местах — внутри карточки задания
(`partials/inline/task_blocks.html`) и шагом ленты цикла
(`partials/inline/cycle_feed.html`). Вторая копия этих функций означала бы, что
любая правка (водяной знак, вердикт, скрытые вопросы) чинится дважды и
разъезжается — ровно то, за чем следит `tests/test_reuse_ratchet.py`.

Точка входа: `window.lrnBlockRender.create({ csrfToken, answered })` возвращает
рендерер с методами `render(block, index)`, `collectAnswers(blocks, scope)` и
свойством `answered` (одна попытка: после ответа поля запираются).

Одна попытка считается **по вопросу**, не по заданию (уточнено 07.09.2026):
поле запирает либо общий `api.answered` (отвечено всё), либо `block.answered`
у конкретного вопроса. Раньше первая отправка запирала форму целиком, и
пропущенный вопрос становился недостижимым — а если он был обязательным,
лента вставала намертво.

Видео отдаёт `window.lrnVideoPlayer.mount` из `_video_player.html` — плеер со
всем поведением (fullscreen, водяной знак, защита от перемотки) второй раз не
переписывается.
*/
(function () {
    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    window.lrnBlockRender = {
        el: el,
        create: function (options) {
            var csrfToken = (options || {}).csrfToken;
            var api = {
                answered: !!(options || {}).answered,
                uid: Math.random().toString(36).slice(2)
            };

            function withTitle(wrap, block) {
                // Не field-label: тот же класс держит подписи полей форм по
                // всему приложению, а здесь заголовок блока должен быть
                // заметнее тела текста под ним (ревью 03.09.2026).
                if (block.title) wrap.appendChild(el('p', 'lrn-blk-title', block.title));
                return wrap;
            }

            function renderText(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-text'), block);
                wrap.appendChild(el('p', 'lrn-blk-body', block.body || ''));
                return wrap;
            }

            function renderPhoto(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-photo'), block);
                var gallery = el('div', 'lrn-blk-gallery');
                (block.images || []).forEach(function (image) {
                    var img = el('img', 'lrn-blk-image');
                    img.src = image.url;
                    img.alt = block.title || 'Изображение к заданию';
                    img.loading = 'lazy';
                    gallery.appendChild(img);
                });
                // Одна картинка занимает всю ширину, несколько — встают сеткой.
                if ((block.images || []).length === 1) gallery.classList.add('is-single');
                wrap.appendChild(gallery);
                if (block.body) wrap.appendChild(el('p', 'video-help', block.body));
                return wrap;
            }

            function renderLink(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-link'), block);
                // Ссылка кнопкой, а не текстом для копирования — решение 17.08
                // по ссылке на созвон, здесь тот же приём.
                var a = el('a', 'btn-outline', block.title || 'Открыть ссылку');
                a.href = block.url;
                a.target = '_blank';
                a.rel = 'noopener noreferrer';
                wrap.appendChild(a);
                if (block.body) wrap.appendChild(el('p', 'video-help', block.body));
                return wrap;
            }

            // Разметка — та же, что у `partials/inline/video.html` (совпадающие
            // `data-role`), только собрана в рантайме: у задачи может быть
            // несколько видео-блоков сразу, у каждого свой endpoint.
            function renderVideo(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-video'), block);
                if (!block.video_embed_endpoint) {
                    wrap.appendChild(el('p', 'video-progress-status is-error', 'Ролик недоступен.'));
                    return wrap;
                }

                var root = el('div', 'lrn-inline-video');
                var statusEl = el('p', 'video-progress-status', 'Загружаем видео…');
                statusEl.setAttribute('aria-live', 'polite');
                statusEl.setAttribute('data-role', 'status');

                var shell = el('div', 'video-shell');
                shell.hidden = true;
                shell.setAttribute('data-role', 'shell');
                shell.setAttribute('aria-label', block.title || 'Видеоурок');

                var frameWrap = el('div', 'video-frame');
                frameWrap.setAttribute('data-role', 'frame-wrap');

                var loading = el('div', 'video-loading', 'Загружаем видео...');
                loading.setAttribute('role', 'status');

                var iframe = el('iframe');
                iframe.setAttribute('data-role', 'iframe');
                iframe.loading = 'eager';
                iframe.allow = 'accelerometer; gyroscope; autoplay; encrypted-media';
                iframe.referrerPolicy = 'strict-origin-when-cross-origin';

                var watermark = el('div', 'video-watermark');
                watermark.setAttribute('aria-hidden', 'true');
                var watermarkCopy = el('div', 'video-watermark-copy');
                watermarkCopy.setAttribute('data-role', 'watermark');
                watermark.appendChild(watermarkCopy);

                var muteButton = el('button', 'video-mute-button', '🔇 Включить звук');
                muteButton.type = 'button';
                muteButton.hidden = true;
                muteButton.setAttribute('data-role', 'mute-btn');
                muteButton.setAttribute('aria-label', 'Включить звук');
                muteButton.title = 'Включить звук';

                var fullscreenButton = el('button', 'video-fullscreen-button', '⛶');
                fullscreenButton.type = 'button';
                fullscreenButton.setAttribute('data-role', 'fullscreen-btn');
                fullscreenButton.setAttribute('aria-label', 'На весь экран');
                fullscreenButton.title = 'На весь экран';

                frameWrap.appendChild(loading);
                frameWrap.appendChild(iframe);
                frameWrap.appendChild(watermark);
                frameWrap.appendChild(muteButton);
                frameWrap.appendChild(fullscreenButton);
                shell.appendChild(frameWrap);

                var progressStatus = el('p', 'video-progress-status');
                progressStatus.hidden = true;
                progressStatus.setAttribute('aria-live', 'polite');
                progressStatus.setAttribute('data-role', 'progress-status');

                root.appendChild(statusEl);
                root.appendChild(shell);
                root.appendChild(progressStatus);
                wrap.appendChild(root);

                window.lrnVideoPlayer.mount(root, {
                    endpoint: block.video_embed_endpoint,
                    csrfToken: csrfToken
                });
                return wrap;
            }

            // Кнопка «Загрузить портфолио» (владелец 03.09.2026): ведёт на
            // готовый экран загрузки работ и возвращает обратно. Своего
            // содержимого у блока нет — только заголовок, пояснение и кнопка.
            function renderPortfolio(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-portfolio'), block);
                if (block.body) wrap.appendChild(el('p', 'lrn-blk-body', block.body));
                var a = el('a', 'btn-blue', 'Загрузить портфолио');
                a.href = block.upload_url || '/upload';
                wrap.appendChild(a);
                if (block.done) {
                    wrap.appendChild(el('p', 'lrn-blk-verdict is-ok', '✓ Работа загружена'));
                } else {
                    wrap.appendChild(el('p', 'video-help', 'Пока работа не загружена, следующие шаги закрыты.'));
                }
                return wrap;
            }

            // Шкала навыков (владелец 03.09.2026): «оцени, насколько ты
            // стрессоустойчивый… ребёнок отмечает 3 из 10». Каждый навык —
            // своя строка с выбором от 1 до 10; верного ответа нет.
            function renderScale(block, index) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-scale'), block);
                if (block.body) wrap.appendChild(el('p', 'lrn-blk-question-body', block.body));
                var max = block.scale_max || 10;
                var saved = block.answer_option_texts || {};
                (block.options || []).forEach(function (option, oi) {
                    var row = el('div', 'lrn-scale-row');
                    row.appendChild(el('span', 'lrn-scale-name', option.text));
                    var select = el('select', 'form-input lrn-scale-input');
                    select.id = 'lrn-scale-' + api.uid + '-' + index + '-' + oi;
                    select.setAttribute('aria-label', option.text);
                    select.setAttribute('data-scale-option', option.id);
                    select.disabled = api.answered || !!block.answered;
                    var empty = el('option', null, '—');
                    empty.value = '';
                    select.appendChild(empty);
                    for (var value = 1; value <= max; value += 1) {
                        var opt = el('option', null, String(value));
                        opt.value = String(value);
                        if (String(saved[option.id] || '') === String(value)) opt.selected = true;
                        select.appendChild(opt);
                    }
                    row.appendChild(select);
                    wrap.appendChild(row);
                });
                return wrap;
            }

            // Уже сданные файлы — той же галереей, что и материалы задания:
            // отдельная сетка под превью означала бы второй набор стилей.
            function submittedGallery(block) {
                var files = block.submitted_files || [];
                if (!files.length) return null;
                var gallery = el('div', 'lrn-blk-gallery');
                files.forEach(function (url) {
                    var img = el('img', 'lrn-blk-image');
                    img.src = url;
                    img.alt = 'Загруженная работа';
                    img.loading = 'lazy';
                    gallery.appendChild(img);
                });
                if (files.length === 1) gallery.classList.add('is-single');
                return gallery;
            }

            // Приём работ прямо в блоке (владелец 07.09.2026: «работы нужно
            // загружать в заданиях»). Один и тот же узел у блока «Загрузить
            // работы» и у «Работы на время» — механика приёма у них общая.
            function uploadForm(block) {
                var wrap = el('div', 'lrn-blk-upload');
                var gallery = submittedGallery(block);
                if (gallery) wrap.appendChild(gallery);
                if (block.submitted_comment) {
                    wrap.appendChild(el('p', 'lrn-blk-body', block.submitted_comment));
                }
                if (block.reviewed) {
                    wrap.appendChild(el('p', 'lrn-blk-verdict is-ok', '✓ Работу проверил куратор'));
                }
                if (block.review_comment) {
                    wrap.appendChild(el('p', 'video-help', block.review_comment));
                }

                var left = (block.max_files || 10) - (block.submitted_files || []).length;
                if (left <= 0) {
                    wrap.appendChild(el('p', 'video-help', 'Загружено максимальное число файлов.'));
                    return wrap;
                }

                var fileId = 'lrn-upl-' + api.uid + '-' + block.id;
                var label = el('label', 'field-label', 'Фото работы (до ' + left + ')');
                label.setAttribute('for', fileId);
                var input = el('input', 'form-input');
                input.type = 'file';
                input.id = fileId;
                input.accept = 'image/*';
                input.multiple = true;

                var commentId = fileId + '-note';
                var commentLabel = el('label', 'field-label', 'Описание работы');
                commentLabel.setAttribute('for', commentId);
                var comment = el('textarea', 'form-input');
                comment.id = commentId;
                comment.rows = 3;
                comment.value = block.submitted_comment || '';

                var send = el('button', 'btn-blue', 'Отправить работу');
                send.type = 'button';
                var note = el('p', 'video-progress-status');
                note.setAttribute('aria-live', 'polite');

                send.addEventListener('click', function () {
                    if (!input.files || !input.files.length) {
                        note.textContent = 'Выберите хотя бы один файл.';
                        note.classList.add('is-error');
                        return;
                    }
                    var data = new FormData();
                    for (var i = 0; i < input.files.length; i += 1) {
                        data.append('photos', input.files[i]);
                    }
                    data.append('comment', comment.value || '');
                    send.disabled = true;
                    note.classList.remove('is-error');
                    note.textContent = 'Загружаем…';
                    fetch(block.upload_endpoint, {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: {'X-CSRF-Token': csrfToken},
                        body: data
                    }).then(function (resp) {
                        return resp.json().then(function (body) {
                            return {ok: resp.ok && body.ok, body: body};
                        });
                    }).then(function (result) {
                        if (!result.ok) throw new Error(result.body.error || '');
                        // Состояние блока и хвост ленты пересчитывает сервер —
                        // перезагружаем, чтобы не расходиться с ним.
                        window.location.reload();
                    }).catch(function (err) {
                        send.disabled = false;
                        note.textContent = (err && err.message)
                            ? err.message
                            : 'Не удалось загрузить. Попробуйте ещё раз.';
                        note.classList.add('is-error');
                    });
                });

                wrap.appendChild(label);
                wrap.appendChild(input);
                wrap.appendChild(commentLabel);
                wrap.appendChild(comment);
                wrap.appendChild(send);
                wrap.appendChild(note);
                return wrap;
            }

            // Блок «Загрузить работы» — приём файлов на месте.
            function renderUpload(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-upload-block'), block);
                if (block.body) wrap.appendChild(el('p', 'lrn-blk-body', block.body));
                if (block.done) {
                    wrap.appendChild(el('p', 'lrn-blk-verdict is-ok', '✓ Работа сдана'));
                }
                wrap.appendChild(uploadForm(block));
                return wrap;
            }

            // Работа на время (владелец 03.09.2026): ученик жмёт «Начать»,
            // рисует и загружает работу здесь же. Превышение лимита не мешает
            // сдать — оно только видно.
            function renderTimed(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-timed'), block);
                if (block.body) wrap.appendChild(el('p', 'lrn-blk-body', block.body));
                var limit = block.time_limit_minutes;
                if (limit) {
                    wrap.appendChild(el('p', 'video-help', 'На работу отводится ' + limit + ' мин.'));
                }
                if (block.done) {
                    wrap.appendChild(el(
                        'p',
                        block.overrun ? 'lrn-blk-verdict is-wrong' : 'lrn-blk-verdict is-ok',
                        block.overrun ? 'Работа сдана, время превышено' : 'Работа сдана вовремя'
                    ));
                    // Форму оставляем: до проверки куратором ученик может
                    // догрузить недостающий лист, не открывая ничего заново.
                    wrap.appendChild(uploadForm(block));
                    return wrap;
                }
                if (!block.started_at) {
                    var startBtn = el('button', 'btn-blue', 'Начать работу');
                    startBtn.type = 'button';
                    var note = el('p', 'video-progress-status');
                    note.setAttribute('aria-live', 'polite');
                    startBtn.addEventListener('click', function () {
                        startBtn.disabled = true;
                        fetch(block.start_endpoint, {
                            method: 'POST',
                            credentials: 'same-origin',
                            headers: {
                                'Accept': 'application/json',
                                'X-CSRF-Token': csrfToken
                            }
                        }).then(function (resp) {
                            if (!resp.ok) throw new Error();
                            // Отсчёт ведёт сервер — перезагружаем, чтобы время
                            // старта пришло из одного источника.
                            window.location.reload();
                        }).catch(function () {
                            startBtn.disabled = false;
                            note.textContent = 'Не удалось начать. Попробуйте ещё раз.';
                            note.classList.add('is-error');
                        });
                    });
                    wrap.appendChild(startBtn);
                    wrap.appendChild(note);
                    return wrap;
                }
                var started = new Date(block.started_at);
                wrap.appendChild(el(
                    'p', 'video-help',
                    'Начато в ' + started.toLocaleTimeString('ru-RU', {hour: '2-digit', minute: '2-digit'})
                ));
                wrap.appendChild(uploadForm(block));
                return wrap;
            }

            function verdictMark(block) {
                // Значком и словом, не одним цветом: цвет читают не все.
                if (block.is_correct === true) return el('span', 'lrn-blk-verdict is-ok', '✓ Верно');
                if (block.is_correct === false) return el('span', 'lrn-blk-verdict is-wrong', '✗ Неверно');
                return null;
            }

            function renderQuestion(block, index) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-question'), block);
                var fieldId = 'lrn-blk-' + api.uid + '-' + index;
                // Это сам вопрос, а не подпись поля — field-label занижал его
                // до заголовка блока, хотя это главный текст (ревью 03.09.2026).
                var label = el('label', 'lrn-blk-question-body', block.body || '');
                label.setAttribute('for', fieldId);
                wrap.appendChild(label);

                if (block.question_type === 'text') {
                    var textarea = el('textarea', 'form-input');
                    textarea.id = fieldId;
                    textarea.rows = 3;
                    textarea.maxLength = 2000;
                    textarea.value = block.answer_text || '';
                    textarea.setAttribute('data-answer-text', block.id);
                    textarea.disabled = api.answered || !!block.answered;
                    wrap.appendChild(textarea);
                    var freeMark = verdictMark(block);
                    if (freeMark) wrap.appendChild(freeMark);
                    return wrap;
                }

                var multiple = block.question_type === 'multiple';
                var chosen = block.answer_option_ids || [];
                (block.options || []).forEach(function (option, oi) {
                    var row = el('label', 'lrn-blk-option');
                    var input = el('input');
                    input.type = multiple ? 'checkbox' : 'radio';
                    input.name = fieldId + (multiple ? '-' + oi : '');
                    input.value = option.id;
                    input.checked = chosen.indexOf(option.id) !== -1;
                    input.setAttribute('data-answer-option', block.id);
                    input.disabled = api.answered || !!block.answered;
                    row.appendChild(input);
                    row.appendChild(el('span', null, option.text));
                    wrap.appendChild(row);
                });
                var mark = verdictMark(block);
                if (mark) wrap.appendChild(mark);
                return wrap;
            }

            var RENDERERS = {
                text: renderText,
                photo: renderPhoto,
                video: renderVideo,
                link: renderLink,
                question: renderQuestion,
                portfolio: renderPortfolio,
                scale: renderScale,
                timed: renderTimed,
                upload: renderUpload
            };

            api.render = function (block, index) {
                var renderer = RENDERERS[block.block_type];
                // Неизвестный тип пропускаем молча: старый браузер с
                // закэшированным шаблоном не должен ронять всю панель, когда
                // на сервере появится новый тип блока.
                return renderer ? renderer(block, index) : null;
            };

            api.collectAnswers = function (blocks, scope) {
                var answers = [];
                (blocks || []).forEach(function (block) {
                    // Уже отвеченный вопрос в отправку не идёт: сервер такой
                    // ответ отклоняет, и вся отправка вместе с ним пропала бы.
                    if (block.answered) return;
                    if (block.block_type === 'scale') {
                        // Оценка приходит текстом варианта: у шкалы «выбран»
                        // каждый навык, которому ученик поставил число.
                        var scaleAnswer = { block_id: block.id, option_ids: [], option_texts: {} };
                        (block.options || []).forEach(function (option) {
                            var field = scope.querySelector('[data-scale-option="' + option.id + '"]');
                            if (!field || !field.value) return;
                            scaleAnswer.option_ids.push(option.id);
                            scaleAnswer.option_texts[option.id] = field.value;
                        });
                        if (scaleAnswer.option_ids.length) answers.push(scaleAnswer);
                        return;
                    }
                    if (block.block_type !== 'question') return;
                    var answer = { block_id: block.id, option_ids: [] };
                    var textField = scope.querySelector('[data-answer-text="' + block.id + '"]');
                    if (textField) answer.text = textField.value;
                    var options = scope.querySelectorAll('[data-answer-option="' + block.id + '"]');
                    Array.prototype.forEach.call(options, function (input) {
                        if (input.checked) answer.option_ids.push(Number(input.value));
                    });
                    answers.push(answer);
                });
                return answers;
            };

            return api;
        }
    };
})();
