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

            var galleryUid = 0;

            // Общая галерея фото-блоков: одинаковый размер превью (квадрат,
            // обрезка по центру — см. .lrn-blk-image в tracker.css) и клик
            // открывает ту же карусель-лайтбокс (partials/lightbox.html), что
            // у остальных фото-гридов в кабинете (photo-grid/photo-zoom-button).
            // До 12.09.2026 превью рисовались голым <img> без кликов — отсюда
            // и разные по высоте картинки, и нераскрывающаяся карусель.
            function photoGallery(urls, alt) {
                var gallery = el('div', 'lrn-blk-gallery');
                galleryUid += 1;
                gallery.setAttribute('data-gallery', 'lrn-blk-' + api.uid + '-' + galleryUid);
                urls.forEach(function (url) {
                    var btn = el('button', 'photo-zoom-button');
                    btn.type = 'button';
                    btn.setAttribute('aria-label', 'Открыть фото');
                    btn.onclick = function () { window.openGallery(btn.firstElementChild); };
                    var img = el('img', 'lrn-blk-image');
                    img.src = url;
                    img.alt = alt;
                    img.loading = 'lazy';
                    btn.appendChild(img);
                    gallery.appendChild(btn);
                });
                // Одна картинка занимает всю ширину, несколько — встают сеткой.
                if (urls.length === 1) gallery.classList.add('is-single');
                return gallery;
            }

            function renderPhoto(block) {
                var wrap = el('div', 'lrn-blk lrn-blk-photo');
                // Кружок выполнения — по аналогии с видео (владелец
                // 12.09.2026): у фото нет своего сигнала вроде `VideoProgress`,
                // отметку ставит и подтверждает сам клик.
                var head = el('div', 'lrn-blk-head');
                if (block.title) head.appendChild(el('p', 'lrn-blk-title', block.title));
                var check = renderBlockCheck(block);
                head.appendChild(check);
                wrap.appendChild(head);

                var urls = (block.images || []).map(function (image) { return image.url; });
                wrap.appendChild(photoGallery(urls, block.title || 'Изображение к заданию'));
                if (block.body) wrap.appendChild(el('p', 'video-help', block.body));

                wireBlockCheck(check, block.confirm_endpoint, block);
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

            // Кружок ручной отметки выполнения в правом верхнем углу карточки
            // (владелец 12.09.2026, видео; распространён на фото-блок в тот
            // же день). Общий для блоков, где отметку ставит сам ученик —
            // источники правды у них разные (видео сверяет `VideoProgress`,
            // фото отмечается сразу), поэтому проверку доступности отметки
            // делает эндпоинт, а не эта функция.
            function renderBlockCheck(block) {
                var check = el('button', 'lrn-blk-check');
                check.type = 'button';
                check.setAttribute('data-role', 'block-check');
                check.setAttribute('aria-pressed', block.done ? 'true' : 'false');
                var doneLabel = 'Выполнено';
                var pendingLabel = 'Отметить выполнение';
                check.setAttribute('aria-label', block.done ? doneLabel : pendingLabel);
                check.title = block.done ? doneLabel : pendingLabel;
                check.textContent = block.done ? '✓' : '';
                if (block.done) {
                    check.classList.add('is-done');
                    check.disabled = true;
                }
                return check;
            }

            // Общий обработчик клика по кружку: шлёт подтверждение на сервер и
            // рисует «Выполнено» только по его ответу — источник правды у
            // видео (`VideoProgress`) и фото (нет проверки, кроме доступа к
            // заданию) разный, поэтому конкретную ошибку показывает вызывающая
            // функция через `onError`.
            function wireBlockCheck(check, endpoint, block, callbacks) {
                callbacks = callbacks || {};
                check.addEventListener('click', function () {
                    if (block.done || !endpoint) return;
                    check.disabled = true;
                    fetch(endpoint, {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: { 'Accept': 'application/json', 'X-CSRF-Token': csrfToken }
                    }).then(function (resp) {
                        return resp.json().then(function (body) {
                            return { ok: resp.ok && body.ok, body: body };
                        });
                    }).then(function (result) {
                        if (!result.ok) throw new Error(result.body && result.body.error);
                        block.done = true;
                        check.classList.add('is-done');
                        check.textContent = '✓';
                        check.setAttribute('aria-pressed', 'true');
                        check.setAttribute('aria-label', 'Выполнено');
                        check.title = 'Выполнено';
                        if (callbacks.onSuccess) callbacks.onSuccess();
                    }).catch(function (err) {
                        check.disabled = false;
                        if (callbacks.onError) callbacks.onError(err);
                    });
                });
            }

            // Разметка — та же, что у `partials/inline/video.html` (совпадающие
            // `data-role`), только собрана в рантайме: у задачи может быть
            // несколько видео-блоков сразу, у каждого свой endpoint.
            function renderVideo(block) {
                var wrap = el('div', 'lrn-blk lrn-blk-video');
                var head = el('div', 'lrn-blk-head');
                if (block.title) head.appendChild(el('p', 'lrn-blk-title', block.title));
                var check = renderBlockCheck(block);
                head.appendChild(check);
                wrap.appendChild(head);

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

                var checkHint = el('p', 'lrn-blk-video-check-hint');
                checkHint.setAttribute('aria-live', 'polite');
                checkHint.hidden = !!block.done;
                // `block.watched` — сервер уже видит по `VideoProgress`, что
                // ролик досмотрен: подсказка не должна звать досматривать то,
                // что уже позади, иначе ученик перематывает заново вслепую.
                checkHint.textContent = block.watched
                    ? 'Ролик просмотрен — отметьте выполнение кружком выше.'
                    : 'Досмотрите ролик до конца, чтобы отметить выполнение.';
                wrap.appendChild(checkHint);

                wireBlockCheck(check, block.confirm_endpoint, block, {
                    onSuccess: function () { checkHint.hidden = true; },
                    onError: function (err) {
                        checkHint.hidden = false;
                        checkHint.classList.add('is-error');
                        checkHint.textContent = err && err.message === 'not_watched'
                            ? 'Досмотрите ролик до конца, чтобы отметить выполнение.'
                            : 'Не удалось отметить. Попробуйте ещё раз.';
                    }
                });

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
            //
            // Ползунок вместо select (владелец 10.09.2026): у range нет
            // пустого значения, поэтому «не тронуто» отслеживается отдельным
            // флагом `dataset.touched`, а не самим `.value` — иначе после
            // рендера все навыки шкалы считались бы отвеченными, хотя
            // ученик ни один не подвинул. `collectAnswers` ниже читает
            // именно этот флаг.
            function renderScale(block, index) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-scale'), block);
                if (block.body) wrap.appendChild(el('p', 'lrn-blk-question-body', block.body));
                var min = typeof block.scale_min === 'number' ? block.scale_min : 0;
                var max = block.scale_max || 10;
                var saved = block.answer_option_texts || {};
                var locked = api.answered || !!block.answered;
                var inputs = [];
                (block.options || []).forEach(function (option, oi) {
                    var row = el('div', 'lrn-scale-row');
                    row.appendChild(el('span', 'lrn-scale-name', option.text));
                    if (option.description) {
                        row.appendChild(el('p', 'trk-hint', option.description));
                    }
                    var control = el('div', 'lrn-scale-control');
                    var input = el('input', 'lrn-scale-input');
                    input.type = 'range';
                    input.min = String(min);
                    input.max = String(max);
                    input.step = '1';
                    input.id = 'lrn-scale-' + api.uid + '-' + index + '-' + oi;
                    input.setAttribute('aria-label', option.text);
                    input.setAttribute('data-scale-option', option.id);
                    input.disabled = locked;
                    // savedValue может быть "0" — валидная оценка, не «ещё не
                    // отвечено». Строка "0" truthy в JS, но проверяем явно, а
                    // не полагаемся на это (владелец 12.09.2026: нижний край
                    // шкалы теперь содержательный ответ, не пустота).
                    var hasSaved = typeof saved[option.id] !== 'undefined' && saved[option.id] !== null;
                    var savedValue = hasSaved ? saved[option.id] : null;
                    var valueLabel = el('span', 'lrn-scale-value', hasSaved ? String(savedValue) : '—');
                    if (hasSaved) {
                        input.value = String(savedValue);
                        input.dataset.touched = 'true';
                    } else {
                        input.value = String(Math.round((min + max) / 2));
                    }
                    input.addEventListener('input', function () {
                        input.dataset.touched = 'true';
                        valueLabel.textContent = input.value;
                    });
                    control.appendChild(input);
                    control.appendChild(valueLabel);
                    row.appendChild(control);
                    if (option.scale_min_label || option.scale_max_label) {
                        var labels = el('div', 'lrn-scale-labels');
                        labels.appendChild(el('span', null, option.scale_min_label || String(min)));
                        labels.appendChild(el('span', null, option.scale_max_label || String(max)));
                        row.appendChild(labels);
                    }
                    wrap.appendChild(row);
                    inputs.push(input);
                });

                // Своя кнопка сохранения (владелец 12.09.2026): «чтобы ученик
                // понял, что его ответы в шкале навыков приняты» — не
                // дожидаться общей формы «Отправить ответы» под всеми
                // блоками задания. Эндпоинт общий
                // (`/cabinet/tracker/tasks/{id}/blocks`), но здесь шлём
                // только ответы этого блока — сервер прекрасно принимает
                // частичную отправку (`submit_cabinet_tracker_task_blocks`:
                // «ответы принимаются частями»), другие блоки задания это
                // не затрагивает.
                if (block.submit_endpoint) {
                    var note = el('p', 'video-progress-status');
                    note.setAttribute('aria-live', 'polite');
                    if (locked) {
                        wrap.appendChild(el(
                            'p', 'lrn-blk-verdict is-ok',
                            'Сохранено — результат в «Личной информации».'
                        ));
                    } else {
                        var saveBtn = el('button', 'btn-blue', 'Сохранить');
                        saveBtn.type = 'button';
                        saveBtn.addEventListener('click', function () {
                            var optionIds = [];
                            var optionTexts = {};
                            inputs.forEach(function (input) {
                                if (input.dataset.touched !== 'true') return;
                                var optionId = Number(input.getAttribute('data-scale-option'));
                                optionIds.push(optionId);
                                optionTexts[optionId] = input.value;
                            });
                            if (!optionIds.length) {
                                note.textContent = 'Сдвиньте хотя бы один ползунок.';
                                note.classList.add('is-error');
                                return;
                            }
                            saveBtn.disabled = true;
                            note.textContent = '';
                            note.classList.remove('is-error');
                            fetch(block.submit_endpoint, {
                                method: 'POST',
                                credentials: 'same-origin',
                                headers: {
                                    'Accept': 'application/json',
                                    'Content-Type': 'application/json',
                                    'X-CSRF-Token': csrfToken
                                },
                                body: JSON.stringify({
                                    answers: [{
                                        block_id: block.id,
                                        option_ids: optionIds,
                                        option_texts: optionTexts
                                    }]
                                })
                            }).then(function (resp) {
                                if (!resp.ok) throw new Error();
                                return resp.json();
                            }).then(function () {
                                block.answered = true;
                                inputs.forEach(function (input) { input.disabled = true; });
                                saveBtn.remove();
                                note.remove();
                                wrap.appendChild(el(
                                    'p', 'lrn-blk-verdict is-ok',
                                    'Сохранено — результат в «Личной информации».'
                                ));
                            }).catch(function () {
                                saveBtn.disabled = false;
                                note.textContent = 'Не удалось сохранить. Попробуйте ещё раз.';
                                note.classList.add('is-error');
                            });
                        });
                        wrap.appendChild(saveBtn);
                        wrap.appendChild(note);
                    }
                }
                return wrap;
            }

            // Уже сданные файлы — той же галереей, что и материалы задания:
            // отдельная сетка под превью означала бы второй набор стилей.
            function submittedGallery(block) {
                var files = block.submitted_files || [];
                if (!files.length) return null;
                return photoGallery(files, 'Загруженная работа');
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

            // Правила школы с галочкой у каждого пункта (владелец 03.09.2026:
            // «прочитать и поставить галочки рядом с этими правилами»).
            // Разметка вариантов та же, что у вопроса (.lrn-blk-option,
            // .lrn-blk-question-body — самостоятельные классы, не вложенные),
            // а класс карточки свой: с чужим `lrn-blk-question` правила
            // получали розовую полосу вопроса и в ленте от него не отличались.
            function renderRules(block, index) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-rules'), block);
                if (block.body) wrap.appendChild(el('p', 'lrn-blk-question-body', block.body));
                var chosen = block.answer_option_ids || [];
                var locked = api.answered || !!block.answered;
                (block.options || []).forEach(function (option, oi) {
                    var row = el('label', 'lrn-blk-option');
                    var input = el('input');
                    input.type = 'checkbox';
                    input.name = 'lrn-rules-' + api.uid + '-' + index + '-' + oi;
                    input.value = option.id;
                    input.checked = chosen.indexOf(option.id) !== -1;
                    input.setAttribute('data-rules-option', block.id);
                    input.disabled = locked;
                    row.appendChild(input);
                    row.appendChild(el('span', null, option.text));
                    wrap.appendChild(row);
                });
                if (!locked) {
                    // Отмечено не всё — сервер такую отправку не сохранит,
                    // и ученик должен понимать почему, а не жать вслепую.
                    wrap.appendChild(el(
                        'p', 'lrn-card-note',
                        'Отметьте все пункты — иначе шаг не закроется.'
                    ));
                }
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
                upload: renderUpload,
                rules: renderRules
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
                            if (!field || field.dataset.touched !== 'true') return;
                            scaleAnswer.option_ids.push(option.id);
                            scaleAnswer.option_texts[option.id] = field.value;
                        });
                        if (scaleAnswer.option_ids.length) answers.push(scaleAnswer);
                        return;
                    }
                    if (block.block_type === 'rules') {
                        // Шлём отмеченное как есть: решение «все или ничего»
                        // принимает сервер, чтобы обход формы ничего не менял.
                        var ruleAnswer = { block_id: block.id, option_ids: [] };
                        var boxes = scope.querySelectorAll('[data-rules-option="' + block.id + '"]');
                        Array.prototype.forEach.call(boxes, function (input) {
                            if (input.checked) ruleAnswer.option_ids.push(Number(input.value));
                        });
                        if (ruleAnswer.option_ids.length) answers.push(ruleAnswer);
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
