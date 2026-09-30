/*
Рендер блоков универсального конструктора — один на все экраны ученика.

Владелец 06.09.2026: восемь вкладок недели заменяются лентой блоков, и блок
теперь рисуется в двух местах — внутри карточки задания
(`partials/inline/task_blocks.html`) и шагом ленты цикла
(`partials/inline/cycle_feed.html`). Вторая копия этих функций означала бы, что
любая правка (водяной знак, вердикт, скрытые вопросы) чинится дважды и
разъезжается — ровно то, за чем следит `tests/test_reuse_ratchet.py`.

Точка входа: `window.lrnBlockRender.create({ csrfToken, answered, titlesOutside })`
возвращает рендерер с методами `render(block, index)`, `collectAnswers(blocks,
scope)` и свойством `answered` (одна попытка: после ответа поля запираются).

`titlesOutside` — подпись блока печатает сам экран, внутри блока её не нужно
(владелец 16.09.2026). Флаг передаёт только лента цикла: `cabinet_learning.html`
рисует заголовок шага `<h3 class="lrn-step-title">`, и та же строка внутри блока
шла второй подряд. Четыре остальных потребителя (панель «Материалы задания» в
трекере и в самой ленте у задания без блоков, предпросмотр «глазами ученика» в
конструкторе дня и в элементах цикла) флаг не передают — там внутренний
заголовок единственная подпись блока.

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

    // Только для *_html-полей — сервер уже прогнал текст через
    // app.tmpl.format_rich_text (html.escape + безопасная разметка),
    // сырой пользовательский ввод сюда не должен попадать (app/tmpl.py).
    function elHtml(tag, className, html) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        node.innerHTML = html || '';
        return node;
    }

    // Ответ на шаг мастера дан, можно жать «Далее»: у шкалы оценены все
    // навыки, у свободного текста что-то написано, у выбора отмечен хоть
    // один вариант. До 30.09.2026 мастер знал только выбор (диагностика), и
    // вопрос с текстом держал бы «Далее» выключенной навсегда.
    function isAnswerFilled(block, answer) {
        if (!answer) return false;
        if (block.block_type === 'scale') return answer.option_ids.length === (block.options || []).length;
        if (block.question_type === 'text') return !!(answer.text || '').trim();
        return answer.option_ids.length > 0;
    }

    window.lrnBlockRender = {
        el: el,
        profileResult: function (profile) {
            var wrap = el('section', 'lrn-blk lrn-blk-profile');
            wrap.setAttribute('aria-label', 'Результат диагностики');
            wrap.appendChild(el('p', 'lrn-blk-score', 'Твоя комбинация: ' + profile.combination));
            wrap.appendChild(el('p', 'lrn-card-note', 'Твой результат диагностики'));
            wrap.appendChild(el('h4', 'lrn-blk-title', profile.title));
            if (profile.traits) wrap.appendChild(el('p', 'lrn-blk-body', profile.traits));
            // formula/architects поддерживают ручную стилизацию преподавателя
            // (жирный/курсив/список/ссылка) — сервер уже прогнал их через
            // format_rich_text (см. `elHtml` выше), поэтому вставляем HTML,
            // а не сырой текст.
            if (profile.formula_html) wrap.appendChild(elHtml('p', 'lrn-blk-body', profile.formula_html));
            if (profile.architects_html) {
                var architectsNote = el('p', 'lrn-card-note', 'Похожие черты можно увидеть в работах: ');
                architectsNote.appendChild(elHtml('span', null, profile.architects_html));
                architectsNote.appendChild(document.createTextNode('. Архитекторы часто сочетают разные профили.'));
                wrap.appendChild(architectsNote);
            }
            return wrap;
        },
        // Мастер прохождения «один вопрос за раз» (диагностика АРХИ-ПРОФИЛЯ,
        // ТЗ «Диагностика» раздел 8). Вопрос рендерит существующий
        // `renderer.render` — вторая копия разметки вопроса не заводится, за
        // этим следит `tests/test_reuse_ratchet.py`. Мастер только переключает
        // видимость уже отрисованных шагов и копит выбранное в самом DOM
        // (radio input не теряет состояние при скрытии), а на сервер уходит
        // один запрос со всеми ответами сразу — диагностика не принимает
        // частичную отправку («чтобы результат был однозначным», см.
        // докстринг `submit_cabinet_tracker_task_blocks`), поэтому и здесь
        // копим ответы локально, а не шлём их по одному.
        //
        // steps: [{ block, container, body }] — `container` прячется между
        // шагами (для ленты цикла это весь `<article class="lrn-step">`, для
        // панели «Материалы задания» — тот же div, что и `body`); `body` —
        // куда рендерится сам вопрос. `callbacks.onFinish(answers, handlers)`
        // получает собранные ответы всех шагов и `handlers.onError()` на
        // случай неудачной отправки.
        //
        // С 30.09.2026 мастером проходится и опрос (`runPoll` ниже), поэтому
        // два параметра: `callbacks.finishLabel` — подпись последней кнопки
        // (у диагностики по умолчанию «Получить результат»), и
        // `callbacks.progress` — строка «Вопрос N из M» над вопросом вместо
        // приписки к заголовку: у вопросов опроса своего заголовка нет.
        runWizard: function (renderer, steps, callbacks) {
            var total = steps.length;
            var finishLabel = callbacks.finishLabel || 'Получить результат';

            function show(index) {
                steps.forEach(function (step, i) {
                    if (step.container) step.container.hidden = i !== index;
                });
            }

            steps.forEach(function (step, index) {
                var body = step.body;
                body.innerHTML = '';
                var node = renderer.render(step.block, index);
                if (node) body.appendChild(node);

                // «Вопрос N из M» — дописываем к уже отрисованному заголовку
                // блока («Вопрос N»), а не заводим вторую строку с тем же
                // числом: заголовок либо серверный `.lrn-step-title` (лента
                // цикла), либо `.lrn-blk-title` внутри самого рендера блока
                // (панель «Материалы задания», `titlesOutside` не передан).
                if (callbacks.progress) {
                    if (total > 1) body.insertBefore(el('p', 'lrn-card-note', 'Вопрос ' + (index + 1) + ' из ' + total), body.firstChild);
                } else {
                    var titleEl = (step.container && step.container.querySelector('.lrn-step-title'))
                        || body.querySelector('.lrn-blk-title');
                    if (titleEl && total > 1) titleEl.textContent = titleEl.textContent + ' из ' + total;
                }

                var isLast = index === total - 1;
                var nav = el('div', 'form-actions');
                var back = el('button', 'btn-outline', 'Назад');
                back.type = 'button';
                back.hidden = index === 0;
                back.addEventListener('click', function () { show(index - 1); });

                var next = el('button', 'btn-blue', isLast ? finishLabel : 'Далее');
                next.type = 'button';
                next.disabled = true;

                var note = el('p', 'video-progress-status');
                note.setAttribute('aria-live', 'polite');

                function checkFilled() {
                    var answered = renderer.collectAnswers([step.block], body);
                    next.disabled = !isAnswerFilled(step.block, answered[0]);
                }
                // `input` — для свободного текста (`change` у поля приходит
                // только при уходе фокуса), `click` — для шкалы: её клетки
                // меняют скрытое поле скриптом, и `change` не возникает.
                body.addEventListener('change', checkFilled);
                body.addEventListener('input', checkFilled);
                body.addEventListener('click', checkFilled);
                checkFilled();

                next.addEventListener('click', function () {
                    if (!isLast) { show(index + 1); return; }
                    var answers = [];
                    steps.forEach(function (s) {
                        answers = answers.concat(renderer.collectAnswers([s.block], s.body));
                    });
                    next.disabled = true;
                    note.textContent = '';
                    note.classList.remove('is-error');
                    callbacks.onFinish(answers, {
                        onError: function () {
                            next.disabled = false;
                            note.textContent = 'Не удалось отправить ответ. Попробуй ещё раз.';
                            note.classList.add('is-error');
                        }
                    });
                });

                nav.appendChild(back);
                nav.appendChild(next);
                body.appendChild(nav);
                body.appendChild(note);
            });

            show(0);
        },
        // Опрос (владелец 30.09.2026): несколько вопросов одним блоком —
        // название, описание, потом вопросы по одному с «Далее», ответы
        // уходят одним запросом в конце. `blocks` — блоки одного опроса по
        // порядку (общий `poll_key`), `host` — куда рисовать. Своей разметки
        // вопроса у опроса нет: шаги рисует тот же `renderer.render`,
        // листает тот же `runWizard`, что у диагностики.
        //
        // options: `showTitle` — печатать название опроса (в ленте его уже
        // напечатал сервер заголовком карточки), `onSubmit(answers,
        // handlers)` — отправка, `finishLabel` — подпись последней кнопки.
        // Возвращает true, если запущен мастер (в опросе есть неотвеченные
        // вопросы) — тогда эти блоки не должны попасть в общую форму ответов.
        runPoll: function (renderer, blocks, host, options) {
            var first = blocks[0] || {};
            if (options.showTitle && first.title) host.appendChild(el('p', 'lrn-blk-title', first.title));
            if (first.poll_intro_html) host.appendChild(elHtml('p', 'lrn-blk-body', first.poll_intro_html));
            // Название опроса лежит на первом вопросе (`title` первого
            // блока) — рендер вопроса напечатал бы его второй раз.
            var questions = blocks.map(function (block) {
                var copy = {};
                Object.keys(block).forEach(function (key) { copy[key] = block[key]; });
                copy.title = null;
                return copy;
            });
            var pending = questions.filter(function (block) { return !block.answered; });
            questions.forEach(function (block, index) {
                if (pending.indexOf(block) !== -1) return;
                var node = renderer.render(block, index);
                if (node) host.appendChild(node);
            });
            if (!pending.length) return false;
            var steps = pending.map(function (block) {
                var body = el('div', 'lrn-blk-step');
                host.appendChild(body);
                return { block: block, container: body, body: body };
            });
            window.lrnBlockRender.runWizard(renderer, steps, {
                finishLabel: options.finishLabel || 'Отправить',
                progress: true,
                onFinish: options.onSubmit
            });
            return true;
        },
        // Блоки опросов задания, сгруппированные по ключу в порядке ленты:
        // `[{ key, blocks }]`. Блоки без ключа (одиночные вопросы, заведённые
        // до опросов) сюда не попадают — они рисуются по-старому.
        pollGroups: function (blocks) {
            var groups = [];
            var byKey = {};
            (blocks || []).forEach(function (block) {
                if (!block.poll_key) return;
                if (!byKey[block.poll_key]) {
                    byKey[block.poll_key] = { key: block.poll_key, blocks: [] };
                    groups.push(byKey[block.poll_key]);
                }
                byKey[block.poll_key].blocks.push(block);
            });
            return groups;
        },
        isAnswerFilled: isAnswerFilled,
        create: function (options) {
            var csrfToken = (options || {}).csrfToken;
            // Локальной переменной, не полем `api`: снаружи флаг никто не
            // читает, в отличие от `answered`.
            var titlesOutside = !!(options || {}).titlesOutside;
            var api = {
                answered: !!(options || {}).answered,
                uid: Math.random().toString(36).slice(2)
            };

            // Любая мутация блока идёт отсюда, и ключ берётся свежим у сервера
            // (`static/js/csrf.js`), а не тот, что попал в разметку при
            // отрисовке страницы. Причина — в докстринге того файла: вкладка
            // ученика живёт дольше ключа, и 26.09.2026 на этом падала отправка
            // работы. `Accept: application/json` ставит хелпер сам, без него
            // отказ приходит HTML-страницей и разбор ответа падает.
            // Фолбэк на прямой fetch — для страниц без base.html (предпросмотр
            // конструктора грузит этот файл отдельным тегом).
            function post(url, options) {
                options = options || {};
                if (window.csrfFetch) return window.csrfFetch(url, options);
                var headers = {'Accept': 'application/json', 'X-CSRF-Token': csrfToken};
                Object.keys(options.headers || {}).forEach(function (key) {
                    headers[key] = options.headers[key];
                });
                var opts = {};
                Object.keys(options).forEach(function (key) { opts[key] = options[key]; });
                opts.credentials = 'same-origin';
                opts.headers = headers;
                return fetch(url, opts);
            }

            // Текст ошибки сервера: `error` у роутов, `detail` у HTTPException.
            function failure(body, fallback) {
                if (window.csrfMessage) return window.csrfMessage(body, fallback);
                return (body && (body.error || body.detail)) || fallback;
            }

            function withTitle(wrap, block) {
                // Не field-label: тот же класс держит подписи полей форм по
                // всему приложению, а здесь заголовок блока должен быть
                // заметнее тела текста под ним (ревью 03.09.2026).
                // `titlesOutside` — экран печатает подпись сам, см. докстринг
                // файла: в ленте она шла второй раз подряд.
                if (!titlesOutside && block.title) wrap.appendChild(el('p', 'lrn-blk-title', block.title));
                return wrap;
            }

            function renderText(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-text'), block);
                wrap.appendChild(elHtml('p', 'lrn-blk-body', block.body_html || ''));
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
                var head = withTitle(el('div', 'lrn-blk-head'), block);
                var check = renderBlockCheck(block);
                head.appendChild(check);
                wrap.appendChild(head);

                var urls = (block.images || []).map(function (image) { return image.url; });
                wrap.appendChild(photoGallery(urls, block.title || 'Изображение к заданию'));
                if (block.body_html) wrap.appendChild(elHtml('p', 'video-help', block.body_html));

                var hint = wrap.appendChild(checkHintLine());
                wireBlockCheck(check, block.confirm_endpoint, block, hint);
                return wrap;
            }

            // Голосовое или кружок преподавателя (владелец 25.09.2026). Файл
            // лежит в S3 и играет штатным плеером браузера; кружок — то же
            // видео, обрезанное в круг (.lrn-blk-note в tracker.css). Отметка
            // выполнения — тем же кружком, что у фото.
            function renderMedia(block) {
                var wrap = el('div', 'lrn-blk lrn-blk-media');
                var head = withTitle(el('div', 'lrn-blk-head'), block);
                var check = renderBlockCheck(block);
                head.appendChild(check);
                wrap.appendChild(head);

                if (block.media_url) {
                    var isNote = block.media_kind === 'note';
                    var media = el(isNote ? 'video' : 'audio', isNote ? 'lrn-blk-note' : 'lrn-blk-audio');
                    media.controls = true;
                    media.preload = 'metadata';
                    if (isNote) media.setAttribute('playsinline', '');
                    media.src = block.media_url;
                    media.setAttribute('aria-label', isNote ? 'Видеосообщение преподавателя' : 'Голосовое преподавателя');
                    wrap.appendChild(media);
                }
                if (block.body_html) wrap.appendChild(elHtml('p', 'video-help', block.body_html));

                var hint = wrap.appendChild(checkHintLine());
                wireBlockCheck(check, block.confirm_endpoint, block, hint);
                return wrap;
            }

            function renderLink(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-link'), block);
                // Ссылка кнопкой, а не текстом для копирования — решение 17.08
                // по ссылке на созвон, здесь тот же приём.
                // Заголовком подписана и кнопка — но только там, где над блоком
                // его нет: в ленте заголовок печатает карточка шага, и кнопка
                // с тем же текстом была бы третьей копией одной строки.
                var a = el('a', 'btn-outline',
                    (!titlesOutside && block.title) || 'Открыть ссылку');
                a.href = block.url;
                a.target = '_blank';
                a.rel = 'noopener noreferrer';
                wrap.appendChild(a);
                if (block.body_html) wrap.appendChild(elHtml('p', 'video-help', block.body_html));
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

            // Строка под кружком: у видео в ней подсказка про просмотр, у фото
            // и голосового она пустая и появляется только с отказом.
            function checkHintLine(text) {
                var hint = el('p', 'lrn-blk-video-check-hint', text || '');
                hint.setAttribute('aria-live', 'polite');
                hint.hidden = !text;
                return hint;
            }

            // Отказ сервера показывается его же словами (`error`/`detail`):
            // повтор отказ не лечит, а общая фраза «Попробуй ещё раз» 28.09.2026
            // полдня прятала 403 «Цикл пройден». Общая фраза осталась только
            // для случая, когда ответа нет вовсе: нет связи или вместо JSON
            // пришла HTML-страница.
            function checkErrorText(err) {
                if (err && err.serverText) return err.serverText;
                return err && err.answered
                    ? 'Не удалось отметить. Обнови страницу и попробуй ещё раз.'
                    : 'Не удалось отметить: нет связи с сервером. Попробуй ещё раз.';
            }

            // Общий обработчик клика по кружку: шлёт подтверждение на сервер и
            // рисует «Выполнено» только по его ответу. Отказ пишет в `hint`;
            // своя расшифровка кода (видео: `not_watched`) — через `errorText`.
            function wireBlockCheck(check, endpoint, block, hint, errorText) {
                check.addEventListener('click', function () {
                    if (block.done || !endpoint) return;
                    check.disabled = true;
                    post(endpoint, { method: 'POST' }).then(function (resp) {
                        return resp.json().then(function (body) {
                            return { ok: resp.ok && body.ok, body: body };
                        }, function () {
                            // Ответ есть, но не JSON (500, страница прокси):
                            // связь в порядке, причины нет.
                            return { ok: false, body: null };
                        });
                    }).then(function (result) {
                        if (!result.ok) {
                            // `detail` бывает списком (ошибки полей FastAPI) —
                            // такой в строку не превращаем.
                            var text = failure(result.body, '');
                            var error = new Error('rejected');
                            error.answered = true;
                            error.serverText = typeof text === 'string' ? text : '';
                            throw error;
                        }
                        block.done = true;
                        check.classList.add('is-done');
                        check.textContent = '✓';
                        check.setAttribute('aria-pressed', 'true');
                        check.setAttribute('aria-label', 'Выполнено');
                        check.title = 'Выполнено';
                        hint.hidden = true;
                    }).catch(function (err) {
                        check.disabled = false;
                        hint.hidden = false;
                        hint.classList.add('is-error');
                        hint.textContent = (errorText && errorText(err)) || checkErrorText(err);
                    });
                });
            }

            // Разметка — та же, что у `partials/inline/video.html` (совпадающие
            // `data-role`), только собрана в рантайме: у задачи может быть
            // несколько видео-блоков сразу, у каждого свой endpoint.
            //
            // Сам плеер вынесен в `videoPlayer`: его же ставит над кнопкой
            // блок «Загрузить портфолио» (видеоинструкция, 17.09.2026).
            function videoPlayer(block, callbacks) {
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

                var cover = el('img', 'video-cover');
                cover.setAttribute('data-role', 'cover');
                cover.alt = '';
                cover.hidden = true;

                var coverPlay = el('button', 'video-cover-play');
                coverPlay.type = 'button';
                coverPlay.setAttribute('data-role', 'cover-play');
                coverPlay.hidden = true;
                coverPlay.innerHTML = '<span aria-hidden="true">▶</span> Смотреть';

                var watermark = el('div', 'video-watermark');
                watermark.setAttribute('aria-hidden', 'true');
                var watermarkCopy = el('div', 'video-watermark-copy');
                watermarkCopy.setAttribute('data-role', 'watermark');
                watermark.appendChild(watermarkCopy);

                var fullscreenButton = el('button', 'video-fullscreen-button', '⛶');
                fullscreenButton.type = 'button';
                fullscreenButton.setAttribute('data-role', 'fullscreen-btn');
                fullscreenButton.setAttribute('aria-label', 'На весь экран');
                fullscreenButton.title = 'На весь экран';

                frameWrap.appendChild(loading);
                frameWrap.appendChild(iframe);
                frameWrap.appendChild(cover);
                frameWrap.appendChild(coverPlay);
                frameWrap.appendChild(watermark);
                frameWrap.appendChild(fullscreenButton);
                shell.appendChild(frameWrap);

                var progressStatus = el('p', 'video-progress-status');
                progressStatus.hidden = true;
                progressStatus.setAttribute('aria-live', 'polite');
                progressStatus.setAttribute('data-role', 'progress-status');

                root.appendChild(statusEl);
                root.appendChild(shell);
                root.appendChild(progressStatus);

                window.lrnVideoPlayer.mount(root, {
                    endpoint: block.video_embed_endpoint,
                    csrfToken: csrfToken,
                    // Инструкция в блоке портфолио и необязательное видео
                    // ничего не ждут от просмотра — предупреждение плеера о
                    // неподтверждённом просмотре там только пугает.
                    watchRequired: block.block_type === 'video' && block.requires_watch !== false,
                    onCompleted: callbacks && callbacks.onCompleted
                });
                return root;
            }

            function renderVideo(block) {
                var wrap = el('div', 'lrn-blk lrn-blk-video');
                var head = withTitle(el('div', 'lrn-blk-head'), block);
                var requiresWatch = block.requires_watch !== false;
                // Кружок есть и у необязательного видео: блок входит в счётчик
                // «Сделано N из M», и без кружка закрыть его было нечем — шаг
                // висел недоделанным навсегда (владелец 18.09.2026). Проверку
                // просмотра сервер для такого блока не делает.
                var check = renderBlockCheck(block);
                head.appendChild(check);
                wrap.appendChild(head);

                // Описание — до плеера: это условие задания, его читают перед
                // просмотром, а под плеером уже стоит подсказка про отметку
                // выполнения. До 25.09.2026 видео-блок описание не показывал
                // вовсе — единственный тип, у которого оно пропадало
                // (у фото, голосового и ссылки строка такая же).
                if (block.body_html) wrap.appendChild(elHtml('p', 'video-help', block.body_html));

                if (!block.video_embed_endpoint) {
                    wrap.appendChild(el('p', 'video-progress-status is-error', 'Ролик недоступен.'));
                    return wrap;
                }

                var checkHint = el('p', 'lrn-blk-video-check-hint');
                checkHint.setAttribute('aria-live', 'polite');
                checkHint.hidden = !!block.done;
                // `block.watched` — сервер уже видит по `VideoProgress`, что
                // ролик досмотрен: подсказка не должна звать досматривать то,
                // что уже позади, иначе ученик перематывает заново вслепую.
                if (!requiresWatch) {
                    checkHint.textContent = 'Посмотри ролик и отметь выполнение кружком выше.';
                } else {
                    checkHint.textContent = block.watched
                        ? 'Ролик просмотрен – отметь выполнение кружком выше.'
                        : 'Досмотри ролик до конца, чтобы отметить выполнение.';
                }
                wrap.appendChild(videoPlayer(block, {
                    onCompleted: function () {
                        block.watched = true;
                        if (block.done) return;
                        checkHint.hidden = false;
                        checkHint.classList.remove('is-error');
                        checkHint.textContent = 'Ролик просмотрен – отметь выполнение кружком выше.';
                    }
                }));
                wrap.appendChild(checkHint);

                wireBlockCheck(check, block.confirm_endpoint, block, checkHint, function (err) {
                    return err && err.serverText === 'not_watched'
                        ? 'Досмотри ролик до конца, чтобы отметить выполнение.'
                        : '';
                });
                return wrap;
            }

            // Кнопка «Загрузить портфолио» (владелец 03.09.2026): ведёт на
            // готовый экран загрузки работ и возвращает обратно. С 17.09.2026
            // над кнопкой может стоять цельная инструкция (владелец): видео,
            // фото и скриншоты-примеры, текст — и уже под ними сама кнопка.
            // Всё необязательно; шаг закрывает только загрузка работы.
            function renderPortfolio(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-portfolio'), block);
                if (block.video_embed_endpoint) {
                    wrap.appendChild(videoPlayer(block));
                } else if (block.video_note) {
                    // Предпросмотр конструктора: блок ещё не сохранён, адреса
                    // плеера нет, и на месте ролика стоит его название. Ученику
                    // сервер `video_note` не отдаёт.
                    wrap.appendChild(el('p', 'prg-hint', block.video_note));
                }
                var urls = (block.images || []).map(function (image) { return image.url; });
                if (urls.length) {
                    wrap.appendChild(photoGallery(urls, block.title || 'Пример к инструкции'));
                }
                if (block.body_html) wrap.appendChild(elHtml('p', 'lrn-blk-body', block.body_html));
                if (block.window_open !== false) {
                    var a = el('a', 'btn-blue', 'Загрузить портфолио');
                    a.href = block.upload_url || '/upload';
                    wrap.appendChild(a);
                    if (block.window_deadline) {
                        wrap.appendChild(el(
                            'p', 'video-help',
                            'Загрузить или заменить работы можно до ' + block.window_deadline + ' по Москве.'
                        ));
                    }
                } else {
                    var disabled = el('button', 'btn-blue', 'Окно загрузки закрыто');
                    disabled.type = 'button';
                    disabled.disabled = true;
                    wrap.appendChild(disabled);
                    wrap.appendChild(el(
                        'p', 'video-help',
                        block.window_deadline
                            ? 'Срок загрузки закончился ' + block.window_deadline + ' по Москве.'
                            : 'Сначала завершите предыдущие шаги.'
                    ));
                }
                if (block.done) {
                    wrap.appendChild(el('p', 'lrn-blk-verdict is-ok', '✓ Работа загружена'));
                } else {
                    wrap.appendChild(el('p', 'video-help', 'Пока работа не загружена, следующие шаги закрыты.'));
                }
                return wrap;
            }

            // Шкала навыков (владелец 03.09.2026): «оцени, насколько ты
            // стрессоустойчивый… ребёнок отмечает 3 из 10». Каждый навык —
            // своя строка с выбором от 0 до 10; верного ответа нет.
            //
            // Закрашенные деления вместо ползунка (владелец 13.09.2026):
            // «выбираем от 0 до 10 с заполнением квадратиков». Клик по
            // делению закрашивает всё до него включительно — та же картинка,
            // что ученик потом видит в «Личной информации».
            //
            // Ноль — отдельная кнопка слева от ряда, а не «снять выделение»
            // повторным кликом: делений между 0 и 10 десять, а значений
            // одиннадцать, и нижний край шкалы — содержательный ответ
            // (владелец 12.09.2026), он обязан выбираться явно.
            //
            // Значение по-прежнему живёт в скрытом `input` с атрибутом
            // `data-scale-option`, а «не тронуто» — в его `dataset.touched`
            // (владелец 10.09.2026): `collectAnswers` и кнопка «Сохранить»
            // ниже читают именно их, иначе ответы молча перестанут уходить
            // на сервер.
            function renderScale(block, index) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-scale'), block);
                if (block.body_html) wrap.appendChild(elHtml('p', 'lrn-blk-question-body', block.body_html));
                var min = typeof block.scale_min === 'number' ? block.scale_min : 0;
                var max = block.scale_max || 10;
                var saved = block.answer_option_texts || {};
                var locked = !!block.edit_reason;
                var inputs = [];
                (block.options || []).forEach(function (option, oi) {
                    var row = el('div', 'lrn-scale-row');
                    row.appendChild(el('span', 'lrn-scale-name', option.text));
                    if (option.description_html) {
                        row.appendChild(elHtml('p', 'trk-hint', option.description_html));
                    }
                    var control = el('div', 'lrn-scale-control');
                    var input = el('input', 'lrn-scale-input');
                    input.type = 'hidden';
                    input.id = 'lrn-scale-' + api.uid + '-' + index + '-' + oi;
                    input.setAttribute('data-scale-option', option.id);
                    // savedValue может быть "0" — валидная оценка, не «ещё не
                    // отвечено». Строка "0" truthy в JS, но проверяем явно, а
                    // не полагаемся на это (владелец 12.09.2026: нижний край
                    // шкалы теперь содержательный ответ, не пустота).
                    // Не числом оценка быть не должна, но в старых ответах и
                    // после ручной правки встречается (`skills_history` такие
                    // точки тоже пропускает). Без проверки ряд показал бы
                    // «NaN» — считаем блок неотвеченным.
                    var hasSaved = typeof saved[option.id] !== 'undefined'
                        && saved[option.id] !== null
                        && Number.isFinite(Number(saved[option.id]));
                    var savedValue = hasSaved ? Number(saved[option.id]) : null;
                    var valueLabel = el('span', 'lrn-scale-value', hasSaved ? String(savedValue) : '—');

                    var cells = el('div', 'lrn-scale-cells');
                    cells.setAttribute('role', 'group');
                    cells.setAttribute('aria-label', option.text);
                    var buttons = [];

                    function paint(value) {
                        buttons.forEach(function (btn) {
                            var own = Number(btn.getAttribute('data-scale-value'));
                            var filled = value !== null && own !== min && own <= value;
                            btn.classList.toggle('is-filled', filled);
                            btn.setAttribute('aria-pressed', value !== null && own === value ? 'true' : 'false');
                        });
                        valueLabel.textContent = value === null ? '—' : String(value);
                    }

                    function choose(value) {
                        input.value = String(value);
                        input.dataset.touched = 'true';
                        paint(value);
                    }

                    // Кнопок на одну больше, чем делений: нулевая слева, затем
                    // по делению на каждое значение от min+1 до max.
                    for (var value = min; value <= max; value++) {
                        var cell = el(
                            'button',
                            value === min ? 'lrn-scale-cell lrn-scale-cell--zero' : 'lrn-scale-cell',
                            value === min ? String(min) : null
                        );
                        cell.type = 'button';
                        cell.disabled = locked;
                        cell.setAttribute('data-scale-value', String(value));
                        cell.setAttribute('aria-pressed', 'false');
                        cell.setAttribute('aria-label', 'оценка ' + value + ' из ' + max);
                        cell.addEventListener('click', function () {
                            choose(Number(this.getAttribute('data-scale-value')));
                        });
                        cells.appendChild(cell);
                        buttons.push(cell);
                    }

                    // Без ответа значение пустое, а не серединное: пустой ряд
                    // не должен выдавать за оценку то, чего ученик не выбирал.
                    // На сервер такое значение всё равно не уйдёт — отправку
                    // решает `dataset.touched`.
                    input.value = hasSaved ? String(savedValue) : '';
                    if (hasSaved) input.dataset.touched = 'true';
                    paint(hasSaved ? savedValue : null);

                    control.appendChild(input);
                    control.appendChild(cells);
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
                // У неотвеченной шкалы опроса своей кнопки нет: её место
                // занимает «Далее» мастера, ответы опроса уходят одним
                // запросом (`runPoll`).
                if (block.submit_endpoint && !block.edit_reason && !(block.poll_key && !block.answered)) {
                    var note = el('p', 'video-progress-status');
                    note.setAttribute('aria-live', 'polite');
                    if (block.answered) {
                        wrap.appendChild(el('p', 'lrn-blk-verdict is-ok', 'Сохранено. До проверки можно изменить оценку.'));
                    }
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
                                note.textContent = 'Сдвинь хотя бы один ползунок.';
                                note.classList.add('is-error');
                                return;
                            }
                            saveBtn.disabled = true;
                            note.textContent = '';
                            note.classList.remove('is-error');
                            post(block.submit_endpoint, {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
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
                                // Шкала опроса в историю навыков не идёт
                                // (`skills_history`, владелец 30.09.2026).
                                wrap.appendChild(el(
                                    'p', 'lrn-blk-verdict is-ok',
                                    block.poll_key
                                        ? 'Сохранено.'
                                        : 'Сохранено — результат в «Личной информации».'
                                ));
                            }).catch(function () {
                                saveBtn.disabled = false;
                                note.textContent = 'Не удалось сохранить. Попробуй ещё раз.';
                                note.classList.add('is-error');
                            });
                        });
                        wrap.appendChild(saveBtn);
                        wrap.appendChild(note);
                }
                if (block.edit_reason) wrap.appendChild(el('p', 'video-help', block.edit_reason));
                return wrap;
            }

            // Уже сданные файлы — той же галереей, что и материалы задания:
            // отдельная сетка под превью означала бы второй набор стилей.
            function submittedGallery(block) {
                var files = block.submitted_files || [];
                if (!files.length) return null;
                return photoGallery(files.map(function (file) { return file.url; }), 'Загруженная работа');
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
                    wrap.appendChild(el('p', 'lrn-blk-verdict is-ok', '✓ Работу проверил преподаватель'));
                }
                if (block.review_comment_html) {
                    var feedback = el('div', 'lrn-blk-feedback');
                    feedback.appendChild(el('strong', 'lrn-blk-feedback-title', 'Комментарий преподавателя'));
                    feedback.appendChild(elHtml('div', 'lrn-blk-feedback-text', block.review_comment_html));
                    wrap.appendChild(feedback);
                }
                if (block.score !== null && block.score !== undefined) {
                    wrap.appendChild(el('p', 'lrn-blk-feedback-title', 'Оценка: ' + block.score + ' / 100'));
                }
                if (block.feedback_url) {
                    var feedbackLink = el('a', 'btn-outline', 'Открыть обратную связь');
                    feedbackLink.href = block.feedback_url;
                    wrap.appendChild(feedbackLink);
                }

                if (block.edit_reason) {
                    wrap.appendChild(el('p', 'video-help', block.edit_reason));
                    return wrap;
                }
                // Срок приёма работ (владелец 27.09.2026). Та же формулировка и
                // тот же формат момента, что у окна портфолио выше — ученик
                // читает одно и то же правило в двух местах одинаково.
                if (block.submit_deadline) {
                    wrap.appendChild(el(
                        'p', 'video-help',
                        'Загрузить или заменить работу можно до ' + block.submit_deadline + ' по Москве.'
                    ));
                }
                (block.submitted_files || []).forEach(function (file, index) {
                    var remove = el('button', 'btn-outline', 'Удалить фото ' + (index + 1));
                    remove.type = 'button';
                    remove.addEventListener('click', function () {
                        remove.disabled = true;
                        post(block.delete_endpoint + '/' + file.id + '/delete', {
                            method: 'POST'
                        }).then(function (resp) {
                            return resp.json().then(function (body) {
                                if (!resp.ok) throw new Error(failure(body, 'Не удалось удалить фото.'));
                                window.location.reload();
                            });
                        }).catch(function (error) {
                            remove.disabled = false;
                            window.alert(error.message);
                        });
                    });
                    wrap.appendChild(remove);
                });

                var left = (block.max_files || 10) - (block.submitted_files || []).length;
                if (left <= 0) wrap.appendChild(el('p', 'video-help', 'Загружено максимальное число файлов.'));

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
                if (block.submitted_files && block.submitted_files.length) {
                    var saveComment = el('button', 'btn-outline', 'Сохранить описание');
                    saveComment.type = 'button';
                    saveComment.addEventListener('click', function () {
                        saveComment.disabled = true;
                        post(block.comment_endpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({comment: comment.value})
                        }).then(function (resp) {
                            return resp.json().then(function (body) {
                                if (!resp.ok) throw new Error(failure(body, 'Не удалось сохранить описание.'));
                                window.location.reload();
                            });
                        }).catch(function (error) {
                            saveComment.disabled = false;
                            window.alert(error.message);
                        });
                    });
                    wrap.appendChild(commentLabel);
                    wrap.appendChild(comment);
                    wrap.appendChild(saveComment);
                }

                var send = el('button', 'btn-blue', 'Отправить работу');
                send.type = 'button';
                var note = el('p', 'video-progress-status');
                note.setAttribute('aria-live', 'polite');

                send.addEventListener('click', function () {
                    if (!input.files || !input.files.length) {
                        note.textContent = 'Выбери хотя бы один файл.';
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
                    post(block.upload_endpoint, {
                        method: 'POST',
                        body: data
                    }).then(function (resp) {
                        // Ответ разбираем через text(): при отказе на уровне
                        // прокси (обрыв, 502) JSON не приходит вовсе, и
                        // resp.json() падал бы своей ошибкой поверх настоящей.
                        return resp.text().then(function (raw) {
                            var body = null;
                            try { body = JSON.parse(raw); } catch (e) { body = null; }
                            return {ok: resp.ok && body && body.ok, body: body, status: resp.status};
                        });
                    }).then(function (result) {
                        if (!result.ok) {
                            // 401 — вход кончился, и повтор тут бесполезен:
                            // человеку надо войти заново, а не «попробовать
                            // ещё раз» (жалоба ученицы 26.09.2026: «уже
                            // несколько раз перезашла, всё равно пишет, что не
                            // удалось» — перезаход в другой вкладке старую
                            // страницу не оживляет).
                            if (result.status === 401) {
                                throw new Error('Вход закончился. Открой кабинет заново и повтори отправку.');
                            }
                            throw new Error(failure(
                                result.body,
                                'Не удалось загрузить (ошибка ' + result.status + '). Попробуй ещё раз.'
                            ));
                        }
                        // Состояние блока и хвост ленты пересчитывает сервер —
                        // перезагружаем, чтобы не расходиться с ним.
                        window.location.reload();
                    }).catch(function (err) {
                        send.disabled = false;
                        note.textContent = (err && err.message)
                            ? err.message
                            : 'Не удалось загрузить. Проверь связь и попробуй ещё раз.';
                        note.classList.add('is-error');
                    });
                });

                if (left > 0) {
                    wrap.appendChild(label);
                    wrap.appendChild(input);
                    if (!block.submitted_files || !block.submitted_files.length) {
                        wrap.appendChild(commentLabel);
                        wrap.appendChild(comment);
                    }
                    wrap.appendChild(send);
                    wrap.appendChild(note);
                }
                return wrap;
            }

            // Блок «Домашнее задание» (тип upload) — приём файлов на месте.
            function renderUpload(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-upload-block'), block);
                if (block.body_html) wrap.appendChild(elHtml('p', 'lrn-blk-body', block.body_html));
                if (block.done) {
                    wrap.appendChild(el('p', 'lrn-blk-verdict is-ok', '✓ Работа сдана'));
                }
                wrap.appendChild(uploadForm(block));
                return wrap;
            }

            // Фото + сдача работы (владелец 12.09.2026): фото-задание — та же
            // галерея, что у renderPhoto, приём результата — та же форма, что
            // у renderUpload. Своего кружка подтверждения нет: блок закрывает
            // сама сдача, как и «Домашнее задание».
            function renderPhotoUpload(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-photo-upload'), block);
                var urls = (block.images || []).map(function (image) { return image.url; });
                if (urls.length) {
                    wrap.appendChild(photoGallery(urls, block.title || 'Изображение к заданию'));
                }
                if (block.body_html) wrap.appendChild(elHtml('p', 'lrn-blk-body', block.body_html));
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
                if (block.body_html) wrap.appendChild(elHtml('p', 'lrn-blk-body', block.body_html));
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
                        post(block.start_endpoint, { method: 'POST' }).then(function (resp) {
                            if (!resp.ok) throw new Error();
                            // Отсчёт ведёт сервер — перезагружаем, чтобы время
                            // старта пришло из одного источника.
                            window.location.reload();
                        }).catch(function () {
                            startBtn.disabled = false;
                            note.textContent = 'Не удалось начать. Попробуй ещё раз.';
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
                var wrap = el('div', 'lrn-blk lrn-blk-question');
                // Название/описание диагностики (владелец 24.09.2026, третий
                // раунд) — сервер кладёт их только на первый вопрос диагностики
                // (`cabinet_tracker.py`), показываем один раз перед ним, теми
                // же классами, что и у обычного текстового блока — не заводим
                // новых ради `test_reuse_ratchet.py`.
                if (block.diagnostic_intro_title) wrap.appendChild(el('p', 'lrn-blk-title', block.diagnostic_intro_title));
                if (block.diagnostic_intro_body_html) wrap.appendChild(elHtml('p', 'lrn-blk-body', block.diagnostic_intro_body_html));
                withTitle(wrap, block);
                var fieldId = 'lrn-blk-' + api.uid + '-' + index;
                // Это сам вопрос, а не подпись поля — field-label занижал его
                // до заголовка блока, хотя это главный текст (ревью 03.09.2026).
                var label = elHtml('label', 'lrn-blk-question-body', block.body_html || '');
                label.setAttribute('for', fieldId);
                wrap.appendChild(label);

                if (block.question_type === 'text') {
                    var textarea = el('textarea', 'form-input');
                    textarea.id = fieldId;
                    textarea.rows = 3;
                    textarea.maxLength = 2000;
                    textarea.value = block.answer_text || '';
                    textarea.setAttribute('data-answer-text', block.id);
                    textarea.disabled = !!block.edit_reason;
                    wrap.appendChild(textarea);
                    var freeMark = verdictMark(block);
                    if (freeMark) wrap.appendChild(freeMark);
                    if (block.edit_reason) wrap.appendChild(el('p', 'video-help', block.edit_reason));
                    return wrap;
                }

                var multiple = block.question_type === 'multiple';
                var chosen = block.answer_option_ids || [];
                (block.options || []).forEach(function (option, oi) {
                    var row = el('label', 'lrn-blk-option');
                    if (option.description) row.classList.add('lrn-blk-option--detailed');
                    if (block.is_archi_profile) row.classList.add('lrn-blk-option--diagnostic');
                    var input = el('input');
                    input.type = multiple ? 'checkbox' : 'radio';
                    input.name = fieldId + (multiple ? '-' + oi : '');
                    input.value = option.id;
                    input.checked = chosen.indexOf(option.id) !== -1;
                    input.setAttribute('data-answer-option', block.id);
                    input.disabled = !!block.edit_reason;
                    row.appendChild(input);
                    var optionCopy;
                    if (option.description) {
                        optionCopy = el('span');
                        optionCopy.appendChild(el('strong', null, option.text));
                        optionCopy.appendChild(el('span', 'lrn-card-note', option.description));
                    } else {
                        // Текст варианта поддерживает ручную стилизацию (диагностика
                        // АРХИ-ПРОФИЛЯ и обычные вопросы) — сервер прогоняет его через
                        // format_rich_text в `text_html`.
                        optionCopy = elHtml('span', null, option.text_html || '');
                    }
                    row.appendChild(optionCopy);
                    wrap.appendChild(row);
                });
                var mark = verdictMark(block);
                if (mark) wrap.appendChild(mark);
                if (block.edit_reason) wrap.appendChild(el('p', 'video-help', block.edit_reason));
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
                if (block.body_html) wrap.appendChild(elHtml('p', 'lrn-blk-question-body', block.body_html));
                var chosen = block.answer_option_ids || [];
                var locked = !!block.edit_reason || !!block.answered;
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
                        'Отметь все пункты – иначе шаг не закроется.'
                    ));
                }
                return wrap;
            }

            // Сравнение работ (Лиза 27.09.2026). Ученик видит пару работ и
            // нажимает на ту, что наберёт больше баллов: выбранная остаётся,
            // к ней приходит следующая по порядку загрузки. Пары перебираются
            // здесь, на сервер уходит только победившая работа — одной
            // попыткой, до отправки можно начать заново.
            //
            // Выбор преподавателя (`pick_url`) сервер отдаёт только вместе с
            // ответом ученика, поэтому подсказать его этот код не может.
            //
            // В карточке две кнопки: нажатие на работу — выбор, лупа в углу
            // открывает её крупно в общем лайтбоксе (partials/lightbox.html).
            function renderCompare(block) {
                var wrap = withTitle(el('div', 'lrn-blk lrn-blk-compare'), block);
                if (block.body_html) wrap.appendChild(elHtml('p', 'lrn-blk-body', block.body_html));
                var works = (block.images || []).map(function (image) { return image.url; });
                if (block.trainer) {
                    wrap.appendChild(el(
                        'p', 'lrn-card-note',
                        'Тренажёр: выбор никуда не сохраняется, ученики его не видят.'
                    ));
                }
                var stage = el('div', 'lrn-cmp');
                wrap.appendChild(stage);

                function workName(url) {
                    var index = works.indexOf(url);
                    return index < 0 ? 'Работа' : 'Работа №' + (index + 1);
                }

                function zoomButton(urls, start) {
                    var btn = el('button', 'lrn-cmp-zoom', '🔍');
                    btn.type = 'button';
                    btn.setAttribute('aria-label', 'Увеличить: ' + workName(urls[start]).toLowerCase());
                    btn.addEventListener('click', function () {
                        if (!window.openLightboxGroup) return;
                        window.openLightboxGroup(urls.map(function (url) {
                            return { full: url, thumb: url, alt: workName(url) };
                        }), start);
                    });
                    return btn;
                }

                function workImage(url) {
                    var img = el('img', 'lrn-cmp-img');
                    img.src = url;
                    img.alt = workName(url);
                    img.loading = 'lazy';
                    return img;
                }

                // Работа с подписью, без выбора — для финала и итога.
                function workFigure(url, caption) {
                    var figure = el('figure', 'lrn-cmp-card');
                    figure.appendChild(workImage(url));
                    figure.appendChild(el('figcaption', 'lrn-cmp-name', caption));
                    figure.appendChild(zoomButton([url], 0));
                    return figure;
                }

                function showResult(chosenUrl, matched, pickUrl, fresh) {
                    stage.innerHTML = '';
                    stage.appendChild(el(
                        'p', matched ? 'lrn-blk-verdict is-ok' : 'lrn-blk-verdict is-neutral',
                        matched ? '✓ Совпало с выбором преподавателя' : 'Не совпало с выбором преподавателя'
                    ));
                    var pair = el('div', 'lrn-cmp-pair');
                    pair.appendChild(workFigure(chosenUrl, 'Твой выбор – ' + workName(chosenUrl).toLowerCase()));
                    if (!matched && pickUrl) {
                        pair.appendChild(workFigure(pickUrl, 'Выбор преподавателя – ' + workName(pickUrl).toLowerCase()));
                    }
                    stage.appendChild(pair);
                    if (block.trainer) {
                        // «Продолжить» перезагружает страницу — в конструкторе
                        // это потеря несохранённых правок. Тренажёр начинается
                        // заново здесь же.
                        var again = el('button', 'btn-outline', 'Пройти ещё раз');
                        again.type = 'button';
                        again.addEventListener('click', function () { showPair(start); });
                        stage.appendChild(again);
                    } else if (fresh) {
                        // Шаги ниже открывает сервер — обновляем ленту по
                        // кнопке, а не сразу: сделанный шаг в ленте
                        // сворачивается, и на телефоне результат пропал бы
                        // раньше, чем его успели прочитать.
                        var next = el('button', 'btn-blue', 'Продолжить');
                        next.type = 'button';
                        next.addEventListener('click', function () { window.location.reload(); });
                        stage.appendChild(next);
                    }
                }

                if (block.chosen_url) {
                    showResult(block.chosen_url, !!block.matched, block.pick_url, false);
                    return wrap;
                }
                if (block.edit_reason) {
                    if (works.length) wrap.appendChild(photoGallery(works, 'Работа для сравнения'));
                    wrap.appendChild(el('p', 'video-help', block.edit_reason));
                    return wrap;
                }
                // Предпросмотр «глазами ученика» отдаёт несохранённый блок: ни
                // адреса отправки, ни пары от сервера. Раньше это уводило в
                // галерею всех работ разом — преподаватель видел не тот экран,
                // что ученик (владелец 29.09.2026). Первая пара у ученика без
                // начатого турнира всегда №1 против №2 (`compare_progress`),
                // её и показываем.
                var start = block.progress || (works.length >= 2 ? {
                    champion_url: works[0],
                    challenger_url: works[1],
                    step: 1,
                    total: works.length - 1
                } : null);
                if (!start) {
                    stage.appendChild(el('p', 'video-help', 'Работы для сравнения ещё не загружены.'));
                    return wrap;
                }

                // Турнир ведёт сервер (владелец 28.09.2026): каждое нажатие
                // сохраняется сразу и окончательно, в ответ приходит следующая
                // пара. Поэтому после перезагрузки ученик там же, где был.
                var busy = false;
                var note = el('p', 'video-progress-status');
                note.setAttribute('aria-live', 'polite');

                function preload(url) {
                    if (url) (new Image()).src = url;
                }

                function pickCard(url, other, pair, step) {
                    var card = el('div', 'lrn-cmp-card');
                    var choose = el('button', 'lrn-cmp-choose');
                    choose.type = 'button';
                    choose.setAttribute('aria-label', 'Выбрать: ' + workName(url).toLowerCase());
                    choose.appendChild(workImage(url));
                    choose.appendChild(el('span', 'lrn-cmp-name', workName(url)));
                    choose.addEventListener('click', function () { choosePair(url, pair, step); });
                    card.appendChild(choose);
                    card.appendChild(zoomButton([url, other], 0));
                    return card;
                }

                function showPair(state) {
                    stage.innerHTML = '';
                    stage.appendChild(el(
                        'p', 'lrn-card-note',
                        'Пара ' + state.step + ' из ' + state.total
                        + '. Нажми на работу, которая наберёт больше баллов. Выбор сразу сохраняется, '
                        + 'переиграть его нельзя. Выбранная останется, рядом появится следующая.'
                    ));
                    var pair = el('div', 'lrn-cmp-pair');
                    pair.appendChild(pickCard(state.champion_url, state.challenger_url, pair, state.step));
                    pair.appendChild(pickCard(state.challenger_url, state.champion_url, pair, state.step));
                    stage.appendChild(pair);
                    note.textContent = '';
                    note.classList.remove('is-error');
                    stage.appendChild(note);
                    preload(works[state.step + 1]);
                }

                // Тренажёр преподавателя (владелец 29.09.2026, по образцу
                // тренажёра диагностики): тот же экран ученика, но турнир идёт
                // здесь, на сервер не уходит ничего. Шаги — зеркало
                // `save_compare_step`: выбранная работа против следующей по
                // порядку, на последней паре — сверка с отметкой «Мой выбор».
                function trainerStep(url, step, total) {
                    if (step < total) {
                        showPair({
                            champion_url: url,
                            challenger_url: works[step + 1],
                            step: step + 1,
                            total: total
                        });
                    } else {
                        showResult(url, url === block.pick_url, block.pick_url, false);
                    }
                }

                function choosePair(url, pair, step) {
                    if (block.trainer) {
                        trainerStep(url, step, start.total);
                        return;
                    }
                    if (busy || !block.submit_endpoint) return;
                    busy = true;
                    pair.querySelectorAll('.lrn-cmp-choose').forEach(function (btn) { btn.disabled = true; });
                    note.classList.remove('is-error');
                    note.textContent = 'Сохраняем…';
                    post(block.submit_endpoint, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ image_url: url, step: step })
                    }).then(function (resp) {
                        return resp.json().catch(function () { return null; }).then(function (body) {
                            return { ok: resp.ok && body && body.ok, body: body };
                        });
                    }).then(function (result) {
                        if (!result.ok) {
                            throw new Error(failure(result.body, 'Не удалось сохранить выбор. Попробуй ещё раз.'));
                        }
                        busy = false;
                        var body = result.body;
                        if (body.finished) {
                            block.done = true;
                            showResult(body.chosen_url, !!body.matched, body.pick_url, true);
                        } else {
                            showPair(body);
                        }
                    }).catch(function (err) {
                        busy = false;
                        pair.querySelectorAll('.lrn-cmp-choose').forEach(function (btn) { btn.disabled = false; });
                        note.textContent = (err && err.message)
                            ? err.message
                            : 'Не удалось сохранить выбор. Проверь связь и попробуй ещё раз.';
                        note.classList.add('is-error');
                    });
                }

                showPair(start);
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
                rules: renderRules,
                photo_upload: renderPhotoUpload,
                media: renderMedia,
                compare: renderCompare
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
                    if (block.edit_reason || (block.answered && block.block_type === 'rules')) return;
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
