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
                    textarea.disabled = api.answered;
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
                    input.disabled = api.answered;
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
                portfolio: renderPortfolio
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
