(function () {
    window.lrnVideoPlayer = window.lrnVideoPlayer || {};

    // `sourceUrl` приходит из данных плеера (`player_js_url`): при включённом
    // мосте это зеркало, иначе прежний адрес Bunny. Файл через мост идёт
    // байт в байт, поэтому SRI-хэш ниже общий для обоих адресов.
    function loadPlayerJs(sourceUrl) {
        if (window.playerjs && window.playerjs.Player) return Promise.resolve();
        if (window.__lrnPlayerJsPromise) return window.__lrnPlayerJsPromise;
        window.__lrnPlayerJsPromise = new Promise(function (resolve, reject) {
            var script = document.createElement('script');
            script.src = sourceUrl || 'https://assets.mediadelivery.net/playerjs/player-0.1.0.min.js';
            script.integrity = 'sha384-FzNVGZdy6ImmE/3LFewUFSxAVlmjM0wP4aKlUJYalPvzGkIEva94s2WZgmeQPVvC';
            script.crossOrigin = 'anonymous';
            script.onload = function () { resolve(); };
            script.onerror = function () { reject(new Error('playerjs_load_failed')); };
            document.head.appendChild(script);
        });
        return window.__lrnPlayerJsPromise;
    }

    // Карта прохода с сервера (`video_progress.py::watch_map`, 09.10.2026):
    // какие куски ролика не просмотрены. По ней — строка и полоска под плеером
    // (`renderWatchMap`) и текст отказа кружка (`task-blocks-render.js`).
    // До неё ученик слышал только «досмотри до конца»: прод 09.10.2026,
    // ученица id 202 — 16 отказов кружка при пропущенном куске 1:07–1:40,
    // она перематывала в конец. Хвост — кусок до самого конца ролика, его
    // смотрят по порядку; кнопка «Досмотреть» ведёт только к дыре позади.
    var GAP_LEAD_SECONDS = 5;
    function clock(seconds) {
        var whole = Math.max(0, Math.floor(seconds));
        var hours = Math.floor(whole / 3600);
        var minutes = Math.floor((whole % 3600) / 60);
        var rest = String(whole % 60).padStart(2, '0');
        return hours > 0 ? hours + ':' + String(minutes).padStart(2, '0') + ':' + rest : minutes + ':' + rest;
    }
    function isTail(map, gap) {
        return gap[1] >= map.duration - 0.05;
    }
    // Позиция плеера дальше начала хвоста больше чем на столько — ученик
    // перемотал хвост, а не досматривает его по порядку.
    var TAIL_SKIPPED_SECONDS = 15;
    // `position` — где сейчас плеер (есть только у подсказки под плеером, у
    // отказа кружка её нет). Прод 11.10.2026, ученик id 265: перетащил
    // ползунок 1,5-часовой записи в конец и видел только «Не просмотрено:
    // с 0:19 до конца» — ни что делать, ни кнопки, чтобы вернуться (кнопка
    // была только у дыры позади). 25 отказов кружка за 12 минут.
    window.lrnVideoPlayer.gapsAdvice = function (map, position) {
        if (!map || !map.duration || !map.gaps) return null;
        if (map.covered >= map.threshold) {
            // Просмотрено достаточно, зачёт ждёт только позицию у конца
            // (`evaluate_watch`: покрытие и позиция у порога).
            return {
                label: 'Пропусков больше нет – перемотай в конец ролика, и просмотр засчитается.',
                circle: 'Пропусков больше нет – перемотай ролик в конец, и кружок поставится.',
                jumpTo: Math.min(map.duration - 1, map.threshold + 2),
                jumpLabel: 'К концу ролика'
            };
        }
        if (!map.gaps.length) return null;
        var parts = map.gaps.map(function (gap) {
            return isTail(map, gap)
                ? 'с ' + clock(gap[0]) + ' до конца'
                : clock(gap[0]) + '–' + clock(gap[1]);
        });
        var shown = parts.slice(0, 3).join(', ')
            + (parts.length > 3 ? ' и ещё ' + (parts.length - 3) : '');
        var hole = map.gaps.filter(function (gap) { return !isTail(map, gap); })[0];
        if (hole) {
            var holeJump = Math.max(0, hole[0] - GAP_LEAD_SECONDS);
            return {
                label: 'Не просмотрено: ' + shown + '. Перемотанное не засчитывается.',
                circle: 'Кружок не поставлен: не досмотрено ' + shown + '. Нажми «Досмотреть с '
                    + clock(holeJump) + '» под роликом, посмотри пропущенное без перемотки'
                    + ' и нажми кружок ещё раз.',
                jumpTo: holeJump,
                jumpLabel: 'Досмотреть с ' + clock(holeJump)
            };
        }
        var tailStart = map.gaps[0][0];
        var skippedTail = typeof position === 'number' && position > tailStart + TAIL_SKIPPED_SECONDS;
        var tailJump = Math.max(0, tailStart - GAP_LEAD_SECONDS);
        return {
            label: skippedTail
                ? 'Не просмотрено: ' + shown + '. Перемотку система не засчитывает – вернись к '
                    + clock(tailStart) + ' и смотри по порядку, ускорять можно до 2×.'
                : 'Не просмотрено: ' + shown,
            circle: 'Кружок не поставлен: не досмотрено ' + shown + '. Включи ролик с '
                + clock(tailStart) + ' и смотри до конца без перемотки, ускорять можно до 2×.'
                + ' Потом нажми кружок ещё раз.',
            jumpTo: skippedTail ? tailJump : null,
            jumpLabel: skippedTail ? 'Вернуться к ' + clock(tailJump) : ''
        };
    };

    // Скрипт грузится с каждым вхождением партиала (иногда и дважды на одной
    // странице, если у задачи и вкладка «Видео», и блоки с видео) —
    // переопределение той же функции идемпотентно, как и `loadPlayerJs` выше.
    window.lrnVideoPlayer.mount = function (root, options) {
        var endpoint = options && options.endpoint;
        var csrfToken = options && options.csrfToken;
        var preloadedData = options && options.data;
        var watchRequired = !(options && options.watchRequired === false);
        var onCompleted = options && typeof options.onCompleted === 'function'
            ? options.onCompleted
            : null;

        var statusEl = root.querySelector('[data-role="status"]');
        var shell = root.querySelector('[data-role="shell"]');
        var frameWrap = root.querySelector('[data-role="frame-wrap"]');
        var iframe = root.querySelector('[data-role="iframe"]');
        var cover = root.querySelector('[data-role="cover"]');
        var coverPlay = root.querySelector('[data-role="cover-play"]');
        var coverFallbackTimer = null;
        var activePlayer = null;
        var watermark = root.querySelector('[data-role="watermark"]');
        var fullscreenButton = root.querySelector('[data-role="fullscreen-btn"]');
        var progressStatus = root.querySelector('[data-role="progress-status"]');

        function showError(message) {
            statusEl.hidden = false;
            statusEl.textContent = message;
            statusEl.classList.add('is-error');
        }

        if (preloadedData) {
            // Данные уже проверены и собраны сервером при рендере страницы —
            // ошибку недоступности он в этом случае отражает кодом ответа
            // страницы (503) и своей веткой шаблона, сюда попадает только
            // успешный случай.
            statusEl.hidden = true;
            shell.hidden = false;
            populatePlayer(preloadedData);
        } else if (endpoint) {
            fetch(endpoint, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
                .then(function (resp) {
                    if (resp.status === 403) {
                        return resp.json().then(function (body) {
                            throw new Error((body && body.detail) || 'Видео недоступно.');
                        });
                    }
                    if (!resp.ok) throw new Error('Не удалось загрузить видео. Попробуй позже.');
                    return resp.json();
                })
                .then(function (data) {
                    if (!data.ok || !data.player_url) {
                        showError('Видео временно недоступно.');
                        return;
                    }
                    statusEl.hidden = true;
                    shell.hidden = false;
                    populatePlayer(data);
                })
                .catch(function (err) {
                    showError(err.message || 'Не удалось загрузить видео.');
                });
        }

        function showCover() {
            if (coverFallbackTimer) {
                window.clearTimeout(coverFallbackTimer);
                coverFallbackTimer = null;
            }
            if (cover && cover.getAttribute('src')) {
                cover.hidden = false;
                // Кнопка не должна зависеть от Player.js ready: на мобильном
                // iframe может сообщить ready позже, а обложка уже видна.
                if (coverPlay) coverPlay.hidden = false;
                frameWrap.classList.remove('is-started');
            }
        }

        function hideCover() {
            if (coverFallbackTimer) {
                window.clearTimeout(coverFallbackTimer);
                coverFallbackTimer = null;
            }
            if (cover) cover.hidden = true;
            if (coverPlay) coverPlay.hidden = true;
            frameWrap.classList.add('is-started');
        }

        // Кнопка «Смотреть» открывает плеер и больше ничего не делает — так
        // решил владелец 21.09.2026. Раньше она пыталась запустить видео сама:
        // на мобильном перезагружала iframe с `autoplay=true&muted=true`, ждала
        // 8 секунд и, если запуск не случился, показывала красным «Видео не
        // запустилось само». iOS этот автозапуск запрещает (энергосбережение тут
        // ни при чём, проверено на iPhone без него), поэтому предупреждение
        // выскакивало у каждого второго и выглядело поломкой. Теперь путь
        // прямой: обложка уходит, зритель жмёт play внутри самого плеера —
        // этот тап iOS пропускает всегда. Отметка просмотра не страдает, она
        // живёт на событиях `play` и `timeupdate` от плеера.
        if (coverPlay) {
            coverPlay.addEventListener('click', function () {
                hideCover();
            });
        }

        function populatePlayer(data) {
            watermark.innerHTML = '';
            [data.viewer_watermark.name, data.viewer_watermark.username].forEach(function (line) {
                var span = document.createElement('span');
                span.textContent = line;
                watermark.appendChild(span);
            });
            iframe.title = data.video_title || '';
            if (cover) {
                if (data.cover_url) {
                    cover.src = data.cover_url;
                    cover.hidden = false;
                } else {
                    cover.removeAttribute('src');
                    cover.hidden = true;
                }
            }
            showCover();
            iframe.addEventListener('load', function () { frameWrap.classList.add('is-ready'); }, { once: true });

            progressStatus.hidden = false;
            progressStatus.textContent = (data.resume_position_seconds || 0) >= 5
                ? 'Продолжим с сохранённого места.'
                : 'Место просмотра сохраняется автоматически.';

            loadPlayerJs(data.player_js_url).then(function () {
                attachPlayerBehaviour(data);
            }).catch(function () {
                progressStatus.textContent = 'Автосохранение временно недоступно. Видео продолжит работать.';
                progressStatus.classList.add('is-error');
                iframe.src = data.player_url;
                // Player.js может не загрузиться из-за сети, CSP или
                // несовпавшего SRI. Под обложкой при этом уже живёт родной
                // плеер Bunny: через пять секунд открываем к нему доступ.
                coverFallbackTimer = window.setTimeout(hideCover, 5000);
            });
        }

        function attachPlayerBehaviour(data) {
            var playerContainer = frameWrap;
            var watermarkCopy = watermark;
            var watermarkCycleTimer = null;
            var watermarkResizeObserver = null;
            var watermarkBounds = null;
            var watermarkFadeMs = 900;
            var lastWatermarkPoint = null;
            var lastWatermarkCell = -1;
            var pseudoFullscreen = false;
            var rotateRequested = false;
            var rotated = false;
            // Кнопка «Повернуть» — одна на все три разметки плеера (страница
            // ролика, вкладка «Видео», видеоблок конструктора), поэтому её
            // собирает ядро, а не каждый шаблон. Видна только в полноэкранном
            // режиме на вертикально стоящем экране.
            var rotateButton = null;
            if (fullscreenButton) {
                rotateButton = document.createElement('button');
                rotateButton.type = 'button';
                rotateButton.className = 'video-fullscreen-button video-rotate-button';
                rotateButton.setAttribute('data-role', 'rotate-btn');
                rotateButton.textContent = '⟳';
                rotateButton.hidden = true;
                fullscreenButton.parentNode.insertBefore(rotateButton, fullscreenButton);
            }

            function measureWatermarkBounds() {
                if (!watermarkCopy) return;
                // `getBoundingClientRect()`, а не `clientWidth`/`clientHeight` —
                // владелец 02.09.2026 поймал на мобильном надпись, гуляющую
                // выше/ниже видео вместо рамки; `clientWidth`/`clientHeight`
                // округляют до целого пикселя и не учитывают то, что элемент
                // мог измениться в тот же кадр — `getBoundingClientRect()`
                // даёт актуальный размер с учётом фактического рендера.
                // Нулевой/крошечный размер (рамка ещё не размещена в потоке —
                // до первого layout, до `is-ready`) не запоминаем: границы
                // остаются прежними, следующий цикл/ResizeObserver пересчитает.
                var containerRect = playerContainer.getBoundingClientRect();
                // Повёрнутая рамка (кнопка «Повернуть», 05.10.2026): у неё
                // `getBoundingClientRect()` отдаёт экранный прямоугольник, где
                // ширина и высота поменялись местами, а надпись ставится в
                // координатах самой рамки. Здесь нужен размер до поворота.
                if (rotated) {
                    containerRect = {
                        width: playerContainer.offsetWidth,
                        height: playerContainer.offsetHeight
                    };
                }
                if (containerRect.width < 20 || containerRect.height < 20) return;
                // Ролик — 16:9, плеер показывает его через object-fit: contain.
                // В псевдо-полноэкранном режиме на высоком узком телефоне рамка
                // (100vw x 100dvh) намного выше самого ролика, и сверху/снизу
                // остаются чёрные поля — Bunny показывает их внутри своего
                // iframe. Раньше границы дрейфа считались по всей рамке,
                // включая эти поля, и водяной знак реально уходил в чёрную
                // область над и под видимой картинкой, а не оставался на ней
                // (владелец 02.09.2026, «летает выше/ниже видео» на мобильном,
                // видно даже без разворота в альбомную ориентацию — фуллскрин
                // на тач-устройствах включается сразу, см. shouldUsePseudo-
                // Fullscreen). Пересчитываем видимый прямоугольник ролика
                // внутри рамки тем же прижатием, что и у contain, и держим
                // границы дрейфа внутри него, а не всей рамки. В обычном
                // режиме рамка и так 16:9 (CSS aspect-ratio) — здесь это
                // no-op.
                var videoAspect = 16 / 9;
                var frameAspect = containerRect.width / containerRect.height;
                var visibleWidth = containerRect.width;
                var visibleHeight = containerRect.height;
                var visibleOffsetX = 0;
                var visibleOffsetY = 0;
                if (frameAspect > videoAspect) {
                    visibleWidth = containerRect.height * videoAspect;
                    visibleOffsetX = (containerRect.width - visibleWidth) / 2;
                } else if (frameAspect < videoAspect) {
                    visibleHeight = containerRect.width / videoAspect;
                    visibleOffsetY = (containerRect.height - visibleHeight) / 2;
                }
                var containerWidth = visibleWidth;
                var containerHeight = visibleHeight;
                var watermarkWidth = watermarkCopy.offsetWidth;
                var watermarkHeight = watermarkCopy.offsetHeight;
                var sidePadding = Math.max(12, Math.min(24, containerWidth * 0.025));
                var topPadding = Math.max(12, Math.min(24, containerHeight * 0.04));
                // Нижний отступ крупнее прочих: там панель управления Bunny —
                // надпись не должна на неё наезжать.
                var bottomPadding = Math.max(58, Math.min(72, containerHeight * 0.1));
                var minX = visibleOffsetX + sidePadding;
                var maxX = Math.max(minX, visibleOffsetX + containerWidth - watermarkWidth - sidePadding);
                var minY = visibleOffsetY + topPadding;
                var maxY = Math.max(minY, visibleOffsetY + containerHeight - watermarkHeight - bottomPadding);

                watermarkBounds = { minX: minX, maxX: maxX, minY: minY, maxY: maxY };
                // Пересчёт обязан сразу вернуть надпись внутрь новых границ. При
                // выходе из фуллскрина контейнер уменьшается, а у слоя
                // overflow: hidden — старая координата оказывается за краем, и
                // ватермарки попросту не видно до следующего показа.
                placeWatermark(lastWatermarkPoint);
            }

            function placeWatermark(point) {
                if (!watermarkCopy || !watermarkBounds || !point) return;
                var x = Math.min(Math.max(point.x, watermarkBounds.minX), watermarkBounds.maxX);
                var y = Math.min(Math.max(point.y, watermarkBounds.minY), watermarkBounds.maxY);
                lastWatermarkPoint = { x: x, y: y };
                watermarkCopy.style.transform =
                    'translate3d(' + x.toFixed(2) + 'px,' + y.toFixed(2) + 'px,0)';
            }

            function pickWatermarkSpot() {
                if (!watermarkBounds) return null;
                // Четыре угловые зоны оставляют центр кадра свободным. Небольшой
                // случайный сдвиг внутри угла мешает надписи застыть в одной
                // точке, но не уводит её на лицо, чертёж или основной текст.
                var safeCorners = [
                    { x: 0, y: 0 },
                    { x: 1, y: 0 },
                    { x: 0, y: 1 },
                    { x: 1, y: 1 }
                ];
                var cell = Math.floor(Math.random() * safeCorners.length);
                if (cell === lastWatermarkCell) {
                    cell = (cell + 1 + Math.floor(Math.random() * (safeCorners.length - 1)))
                        % safeCorners.length;
                }
                lastWatermarkCell = cell;
                var corner = safeCorners[cell];
                var jitterX = (watermarkBounds.maxX - watermarkBounds.minX) * 0.08 * Math.random();
                var jitterY = (watermarkBounds.maxY - watermarkBounds.minY) * 0.08 * Math.random();
                return {
                    x: corner.x
                        ? watermarkBounds.maxX - jitterX
                        : watermarkBounds.minX + jitterX,
                    y: corner.y
                        ? watermarkBounds.maxY - jitterY
                        : watermarkBounds.minY + jitterY
                };
            }

            function runWatermarkCycle() {
                if (!watermarkCopy) return;
                measureWatermarkBounds();
                placeWatermark(pickWatermarkSpot());
                watermarkCopy.classList.add('is-visible');
                // Видно заметно дольше, чем не видно: пауза нужна, чтобы надпись не
                // мозолила глаз, но при этом почти любой отрезок записи должен
                // застать зрителя на кадре.
                watermarkCycleTimer = window.setTimeout(function () {
                    watermarkCopy.classList.remove('is-visible');
                    watermarkCycleTimer = window.setTimeout(runWatermarkCycle, watermarkFadeMs + 2400);
                }, 5200);
            }

            function startWatermarkDrift() {
                if (!watermarkCopy) return;
                // Идемпотентно: страница возвращается из bfcache уже остановленной,
                // а второй запуск поверх живого цикла дал бы два таймера и два
                // наблюдателя за размером.
                stopWatermarkDrift();
                watermarkCopy.classList.add('is-drifting');

                var reduceMotion = window.matchMedia
                    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                // Тот же цикл, но без плавности: при prefers-reduced-motion надпись
                // просто появляется и пропадает.
                watermarkFadeMs = reduceMotion ? 0 : 900;
                watermarkCopy.style.transitionDuration = watermarkFadeMs + 'ms';

                runWatermarkCycle();

                if ('ResizeObserver' in window) {
                    watermarkResizeObserver = new ResizeObserver(measureWatermarkBounds);
                    watermarkResizeObserver.observe(playerContainer);
                } else {
                    window.addEventListener('resize', measureWatermarkBounds);
                }
            }

            function stopWatermarkDrift() {
                if (watermarkCycleTimer !== null) {
                    window.clearTimeout(watermarkCycleTimer);
                    watermarkCycleTimer = null;
                }
                // Замирает видимой, а не погашенной. Остановка приходит на
                // pagehide, а он срабатывает и при уходе во вкладку-фон, откуда
                // страница возвращается живой: погашенная надпись оставила бы
                // опознанное видео идти вообще без имени зрителя.
                if (watermarkCopy) watermarkCopy.classList.add('is-visible');
                if (watermarkResizeObserver) {
                    watermarkResizeObserver.disconnect();
                    watermarkResizeObserver = null;
                } else {
                    window.removeEventListener('resize', measureWatermarkBounds);
                }
            }

            startWatermarkDrift();

            function fullscreenElement() {
                return document.fullscreenElement || document.webkitFullscreenElement || null;
            }
            function isFullscreenActive() {
                return pseudoFullscreen || fullscreenElement() === playerContainer;
            }
            // `100vh`/`100dvh` в CSS не спасают на части мобильных браузеров
            // (старый WebKit без dvh, встроенные webview VK/Telegram): при
            // видимой адресной строке `100vh` больше реального видимого
            // экрана, кнопка выхода (bottom:8px) уезжает за нижний край, а
            // `overflow:hidden` на body одновременно блокирует скролл до неё —
            // тупик без выхода (баг владельца 31.08.2026). `visualViewport`
            // отдаёт реальную видимую высоту в любой момент, включая смену
            // адресной строки — держим размер синхронным с ней, а не с
            // постоянными vh/dvh.
            //
            // Поворот (владелец 05.10.2026: «хотелось бы, чтобы видео можно
            // было повернуть горизонтально»). Системный разворот экрана из
            // страницы недоступен: `screen.orientation.lock` на iPhone нет
            // вовсе, а на Android он работает только в нативном полном экране,
            // которого у тач-устройств нет (см. shouldUsePseudoFullscreen).
            // Поэтому поворачиваем саму рамку на 90° — вместе с iframe и
            // водяным знаком, слой поверх ролика остаётся тем же. Рамка
            // получает размеры экрана наоборот, поворачивается вокруг левого
            // верхнего угла и сдвигается на свою высоту обратно в кадр.
            // Повернул телефон сам — экран уже горизонтальный, второй поворот
            // не нужен: рамка встаёт ровно, а просьба ждёт возврата в вертикаль.
            function syncPseudoFullscreenSize() {
                if (!pseudoFullscreen) return;
                var vv = window.visualViewport;
                var width = vv ? vv.width : window.innerWidth;
                var height = vv ? vv.height : window.innerHeight;
                rotated = rotateRequested && height > width;
                playerContainer.classList.toggle('is-rotated', rotated);
                if (rotated) {
                    playerContainer.style.width = height + 'px';
                    playerContainer.style.height = width + 'px';
                    playerContainer.style.transformOrigin = 'top left';
                    playerContainer.style.transform = 'rotate(90deg) translateY(-100%)';
                } else {
                    playerContainer.style.width = width + 'px';
                    playerContainer.style.height = height + 'px';
                    playerContainer.style.transformOrigin = '';
                    playerContainer.style.transform = '';
                }
                updateRotateButton(height > width);
            }
            function updateRotateButton(isPortrait) {
                if (!rotateButton) return;
                rotateButton.hidden = !pseudoFullscreen || !(isPortrait || rotated);
                var label = rotated ? 'Вернуть вертикально' : 'Повернуть горизонтально';
                rotateButton.setAttribute('aria-label', label);
                rotateButton.setAttribute('title', label);
                rotateButton.setAttribute('aria-pressed', String(rotated));
            }
            if (rotateButton) {
                rotateButton.addEventListener('click', function () {
                    rotateRequested = !rotated;
                    syncPseudoFullscreenSize();
                    window.requestAnimationFrame(measureWatermarkBounds);
                });
            }
            function enterPseudoFullscreen() {
                if (pseudoFullscreen) return;
                pseudoFullscreen = true;
                playerContainer.classList.add('is-pseudo-fullscreen');
                document.documentElement.classList.add('has-video-pseudo-fullscreen');
                document.body.classList.add('has-video-pseudo-fullscreen');
                syncPseudoFullscreenSize();
                if (window.visualViewport) {
                    window.visualViewport.addEventListener('resize', syncPseudoFullscreenSize);
                    window.visualViewport.addEventListener('scroll', syncPseudoFullscreenSize);
                } else {
                    window.addEventListener('resize', syncPseudoFullscreenSize);
                }
                updateFullscreenButton();
            }
            function exitPseudoFullscreen(restoreFocus) {
                if (!pseudoFullscreen) return;
                pseudoFullscreen = false;
                rotateRequested = false;
                rotated = false;
                playerContainer.classList.remove('is-pseudo-fullscreen');
                playerContainer.classList.remove('is-rotated');
                playerContainer.style.height = '';
                playerContainer.style.width = '';
                playerContainer.style.transformOrigin = '';
                playerContainer.style.transform = '';
                updateRotateButton(false);
                document.documentElement.classList.remove('has-video-pseudo-fullscreen');
                document.body.classList.remove('has-video-pseudo-fullscreen');
                if (window.visualViewport) {
                    window.visualViewport.removeEventListener('resize', syncPseudoFullscreenSize);
                    window.visualViewport.removeEventListener('scroll', syncPseudoFullscreenSize);
                } else {
                    window.removeEventListener('resize', syncPseudoFullscreenSize);
                }
                updateFullscreenButton();
                if (restoreFocus !== false && fullscreenButton) fullscreenButton.focus();
            }
            function updateFullscreenButton() {
                if (!fullscreenButton) return;
                var isFullscreen = isFullscreenActive();
                var label = isFullscreen ? 'Выйти из полноэкранного режима' : 'На весь экран';
                fullscreenButton.setAttribute('aria-label', label);
                fullscreenButton.setAttribute('title', label);
                fullscreenButton.setAttribute('aria-pressed', String(isFullscreen));
                window.requestAnimationFrame(measureWatermarkBounds);
                window.setTimeout(measureWatermarkBounds, 250);
            }
            function reportFullscreenError() {
                showStatus('Не удалось включить полноэкранный режим. Разреши его в настройках браузера.', true);
            }

            var requestFullscreen = playerContainer.requestFullscreen
                || playerContainer.webkitRequestFullscreen;
            var exitFullscreen = document.exitFullscreen
                || document.webkitExitFullscreen;
            function shouldUsePseudoFullscreen() {
                if (!requestFullscreen || !exitFullscreen) return true;
                return window.matchMedia
                    && window.matchMedia('(max-width: 900px), (pointer: coarse)').matches;
            }
            // Вход в полноэкранный режим — только по клику на кнопку (владелец
            // 11.09.2026 отменил автовключение от 31.08.2026: разворот на весь
            // экран без действия ученика мешал). Нативный `requestFullscreen()`
            // иногда всё равно отклоняется (например, если браузер решил, что
            // клик был недостаточно «живым») — `.catch(enterPseudoFullscreen)`/
            // `try…catch` переключают на свой псевдо-полноэкранный режим,
            // которому разрешение браузера не нужно.
            function activateFullscreen() {
                if (pseudoFullscreen || fullscreenElement() === playerContainer) return;
                if (shouldUsePseudoFullscreen()) { enterPseudoFullscreen(); return; }
                try {
                    var requestResult = requestFullscreen.call(playerContainer);
                    if (requestResult && typeof requestResult.catch === 'function') requestResult.catch(enterPseudoFullscreen);
                } catch (error) { enterPseudoFullscreen(); }
            }
            if (fullscreenButton) {
                fullscreenButton.addEventListener('click', function () {
                    if (pseudoFullscreen) { exitPseudoFullscreen(); return; }
                    if (fullscreenElement() === playerContainer) {
                        try {
                            var exitResult = exitFullscreen.call(document);
                            if (exitResult && typeof exitResult.catch === 'function') exitResult.catch(reportFullscreenError);
                        } catch (error) { reportFullscreenError(); }
                        return;
                    }
                    activateFullscreen();
                });
                document.addEventListener('fullscreenchange', updateFullscreenButton);
                document.addEventListener('webkitfullscreenchange', updateFullscreenButton);
                document.addEventListener('keydown', function (event) {
                    if (event.key === 'Escape' && pseudoFullscreen) exitPseudoFullscreen();
                });
                updateFullscreenButton();
            }

            window.addEventListener('pagehide', function () {
                stopWatermarkDrift();
                exitPseudoFullscreen(false);
            });

            var playerUrlEndpoint = data.player_url_endpoint || '';
            var playerUrlTtlSeconds = data.player_url_ttl_seconds || 300;
            var playerUrlMintedAt = Date.now();
            var playerUrlRefreshInFlight = false;
            var playerUrlRefreshDisabled = false;
            var reattachPlayer = null;
            var playerGeneration = 0;
            var isPlaying = false;
            // Какие плееры страницы сейчас играют — общий на страницу список:
            // скрипт подключается с каждым партиалом, поэтому живёт на window.
            var playingPlayers = window.lrnVideoPlayer.playing
                || (window.lrnVideoPlayer.playing = new Set());
            var playerKey = {};
            function setPlaying(value) {
                isPlaying = value;
                if (value) { playingPlayers.add(playerKey); return; }
                if (playingPlayers.delete(playerKey) && playingPlayers.size === 0) {
                    window.dispatchEvent(new Event('lrn-video-idle'));
                }
            }
            var hasEverPlayed = false;
            // Плеер не прислал `ready` (прод 08.10.2026, ученица id 219: телефон
            // с VPN). Видео в рамке играло, а страница не получила ни одного
            // события Player.js — 9 сеансов подряд сервер видел её на 25-й
            // секунде, кружок отказан 9 раз. Хуже того, раз «ролик не
            // запускали», страница раз в 5 минут перевыпускала ссылку и
            // перезагружала плеер посреди просмотра — ролик начинался заново.
            // Молчащий плеер не перезагружаем и честно говорим ученику, почему
            // просмотр не засчитывается.
            var playerSilent = false;
            var playerFaultsReported = {};
            var PLAYER_SILENCE_MS = 20000;

            function showStatus(message, isError) {
                progressStatus.hidden = false;
                progressStatus.textContent = message;
                progressStatus.classList.toggle('is-error', Boolean(isError));
            }

            function isPlayerUrlStale() {
                var safeLifetimeMs = Math.max(30, playerUrlTtlSeconds - 60) * 1000;
                return Date.now() - playerUrlMintedAt > safeLifetimeMs;
            }

            function refreshPlayerUrl() {
                if (!playerUrlEndpoint || playerUrlRefreshInFlight || playerUrlRefreshDisabled) return;
                playerUrlRefreshInFlight = true;
                fetch(playerUrlEndpoint, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
                    .then(function (response) {
                        if (response.status === 401) { playerUrlRefreshDisabled = true; throw new Error('session_expired'); }
                        if (response.status === 403 || response.status === 404) { playerUrlRefreshDisabled = true; throw new Error('access_closed'); }
                        if (!response.ok) throw new Error('refresh_failed');
                        return response.json();
                    })
                    .then(function (body) {
                        if (!body || !body.player_url) throw new Error('refresh_failed');
                        // Свежий подписанный адрес держим в `data`: отсюда его
                        // берёт следующая перезагрузка iframe, иначе она собрала
                        // бы адрес с истёкшим токеном.
                        data.player_url = body.player_url;
                        if (body.ttl_seconds) playerUrlTtlSeconds = body.ttl_seconds;
                        playerUrlMintedAt = Date.now();
                        frameWrap.classList.remove('is-ready');
                        showCover();
                        iframe.addEventListener('load', function () {
                            frameWrap.classList.add('is-ready');
                            if (reattachPlayer) reattachPlayer();
                        }, { once: true });
                        iframe.src = body.player_url;
                    })
                    .catch(function (error) {
                        if (error.message === 'access_closed') showStatus('Доступ к уроку закрыт. Обнови страницу.', true);
                        else if (error.message === 'session_expired') showStatus('Обнови страницу, чтобы продолжить просмотр.', true);
                    })
                    .finally(function () { playerUrlRefreshInFlight = false; });
            }

            // Пока на странице играет любой ролик, соседние плееры ссылку не
            // обновляют: обновление перезагружает плеер Bunny целиком, и на
            // слабой связи он отнимает канал у играющего ролика. Прод
            // 06.10.2026: у ученицы с тремя плеерами на странице ролик вставал
            // на минуту-полторы ровно в моменты таких перезагрузок соседа (раз в
            // 5 минут на каждый простаивающий плеер), жалоба «зависает каждые
            // две минуты». Ссылка обновится, как только воспроизведение встанет.
            function refreshPlayerUrlIfStale() {
                // Молчащий плеер мог играть: страница этого просто не знает.
                // Загруженной рамке свежая ссылка не нужна — куски видео
                // подписаны своим токеном на часы, а не на 5 минут.
                if (playerSilent) return;
                if (playingPlayers.size > 0 || !isPlayerUrlStale()) return;
                refreshPlayerUrl();
            }
            // Только плееры, которые ещё не запускали, — то же условие, что у
            // минутного таймера ниже. Без него пауза перезагружала и сам
            // поставленный на паузу ролик (прод 06.10.2026, 16:43 UTC: ученица
            // нажала паузу, все три плеера перезагрузились, видео 3 минуты не
            // шло). Запущенный плеер обновляет ссылку по возврату на вкладку.
            window.addEventListener('lrn-video-idle', function () {
                if (hasEverPlayed || document.visibilityState !== 'visible') return;
                refreshPlayerUrlIfStale();
            });

            document.addEventListener('visibilitychange', function () {
                if (document.visibilityState === 'visible') refreshPlayerUrlIfStale();
            });
            // Страница видео отдаётся с `Cache-Control: private, no-store`, поэтому
            // возврат из bfcache не гарантирован — чаще браузер просто перезагрузит
            // страницу целиком. Но часть браузеров всё же вернут её живой: `pagehide`
            // уже погасил цикл водяного знака (см. ниже), без перезапуска здесь он
            // остался бы стоять в одной точке — то есть перестал бы мешать записи
            // экрана (владелец 31.08.2026; было в `cabinet_video.html` до переезда
            // 03.09.2026 на общий плеер, но отсутствовало здесь для видео-блока и
            // вкладки «Видео» — перенесено сюда, чтобы у всех мест был один фикс).
            window.addEventListener('pageshow', function (event) {
                if (!event.persisted) return;
                startWatermarkDrift();
                refreshPlayerUrlIfStale();
            });
            window.setInterval(function () {
                if (document.visibilityState !== 'visible' || hasEverPlayed) return;
                refreshPlayerUrlIfStale();
            }, 60000);

            if (!window.playerjs || !window.playerjs.Player) {
                playerUrlRefreshDisabled = true;
                showStatus('Автосохранение временно недоступно. Видео продолжит работать.', true);
                iframe.src = data.player_url;
                // Без player.js событие play из iframe недоступно. Оставляем
                // обложку на пять секунд, затем отдаём управление нативному
                // плееру Bunny.
                coverFallbackTimer = window.setTimeout(hideCover, 5000);
                return;
            }

            var resumeSeconds = data.resume_position_seconds || 0;
            // Номер сеанса — этого экземпляра плеера (07.10.2026). Сервер
            // сравнивает отметку с прошлой отметкой того же сеанса и склеивает
            // просмотренные отрезки всех сеансов: второй плеер того же ролика
            // больше не срезает первому честный кусок. При `reattachPlayer`
            // номер прежний — это та же вкладка, плеер пересоздаётся только
            // ради свежей ссылки; разрыв в позиции сервер увидит сам.
            var sessionId = (window.crypto && typeof window.crypto.randomUUID === 'function')
                ? window.crypto.randomUUID()
                : 's-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
            var currentSeconds = resumeSeconds;
            var durationSeconds = null;
            var lastSavedSeconds = resumeSeconds;
            var lastAutomaticSaveAt = Date.now();
            var saveInFlight = false;
            var pendingProgress = null;
            var savingDisabled = false;
            // Сервер сдвинул позицию назад: до порога не хватило честных
            // секунд (`video_progress.py::watch_shortfall_seconds`, 05.10.2026).
            // Пока висит эта подсказка, «Место просмотра сохранено» её не
            // затирает — иначе ученик снова не поймёт, зачем ролик отмотан.
            var finishHintShown = false;
            var hasReportedSaved = false;
            var completionReported = false;
            // Разовый сбой сохранения ученику не показываем: выкатка
            // перезапускает приложение на 10–20 секунд, и heartbeat (раз в
            // 10 сек) попадает в эту паузу. До 18.09.2026 красная надпись
            // появлялась с первого же сбоя и не уходила после успешных
            // сохранений следом. Порог — три сбоя подряд, около 30 секунд.
            var SAVE_FAILURES_BEFORE_ERROR = 3;
            var saveFailures = 0;

            // Что не просмотрено — полоской и строкой под плеером, с кнопкой
            // к первому пропущенному куску (09.10.2026). Карта приходит со
            // страницей и с каждым ответом на отметку, так что закрашивается
            // по ходу просмотра. Только там, где просмотр проверяют.
            var watchMapEl = null;
            function renderWatchMap(map) {
                if (!watchRequired || !progressStatus) return;
                var advice = map && map.covered > 0
                    ? window.lrnVideoPlayer.gapsAdvice(map, currentSeconds)
                    : null;
                if (!advice) {
                    if (watchMapEl) watchMapEl.hidden = true;
                    return;
                }
                if (!watchMapEl) {
                    watchMapEl = document.createElement('div');
                    watchMapEl.className = 'video-watch-map';
                    var bar = document.createElement('div');
                    bar.className = 'video-watch-map-bar';
                    bar.setAttribute('aria-hidden', 'true');
                    var text = document.createElement('p');
                    text.className = 'video-watch-map-text';
                    text.setAttribute('aria-live', 'polite');
                    var jump = document.createElement('button');
                    jump.type = 'button';
                    jump.className = 'btn-outline video-watch-map-jump';
                    jump.addEventListener('click', function () {
                        var target = Number(jump.getAttribute('data-jump-to'));
                        if (!activePlayer || !Number.isFinite(target)) return;
                        hideCover();
                        seekTo(target);
                    });
                    watchMapEl.appendChild(bar);
                    watchMapEl.appendChild(text);
                    watchMapEl.appendChild(jump);
                    progressStatus.parentNode.insertBefore(watchMapEl, progressStatus.nextSibling);
                }
                var barEl = watchMapEl.querySelector('.video-watch-map-bar');
                barEl.innerHTML = '';
                map.gaps.forEach(function (gap) {
                    var mark = document.createElement('span');
                    mark.className = 'video-watch-map-gap';
                    mark.style.left = (100 * gap[0] / map.duration) + '%';
                    mark.style.width = Math.max(0.5, 100 * (gap[1] - gap[0]) / map.duration) + '%';
                    barEl.appendChild(mark);
                });
                watchMapEl.querySelector('.video-watch-map-text').textContent = advice.label;
                var jumpEl = watchMapEl.querySelector('.video-watch-map-jump');
                jumpEl.hidden = advice.jumpTo === null;
                jumpEl.textContent = advice.jumpLabel;
                jumpEl.setAttribute('data-jump-to', advice.jumpTo === null ? '' : String(advice.jumpTo));
                watchMapEl.hidden = false;
            }
            renderWatchMap(data.watch_map);

            function finiteNumber(value) {
                var number = Number(value);
                return Number.isFinite(number) ? number : null;
            }
            function timingData(raw) {
                if (typeof raw === 'string') {
                    try { raw = JSON.parse(raw); } catch (error) { return null; }
                }
                if (!raw || typeof raw !== 'object') return null;
                return { seconds: finiteNumber(raw.seconds), duration: finiteNumber(raw.duration) };
            }
            function formatTime(seconds) {
                var whole = Math.max(0, Math.floor(seconds));
                var hours = Math.floor(whole / 3600);
                var minutes = Math.floor((whole % 3600) / 60);
                var remainder = whole % 60;
                if (hours > 0) return hours + ':' + String(minutes).padStart(2, '0') + ':' + String(remainder).padStart(2, '0');
                return minutes + ':' + String(remainder).padStart(2, '0');
            }
            function setStatus(message, isError) {
                // Предупреждение о молчащем плеере не затирается ответами на
                // отметки: «Место просмотра сохранено» после него было бы
                // неправдой. Снимает его только дошедший `ready`.
                if (playerSilent) return;
                if (progressStatus.textContent.trim() === message) return;
                showStatus(message, isError);
            }

            function sendProgress(progress, keepalive) {
                if (savingDisabled) return;
                if (saveInFlight) { pendingProgress = progress; return; }
                saveInFlight = true;
                // Ключ берём свежим (`static/js/csrf.js`): при 403 ниже плеер
                // выключает сохранение до конца жизни страницы, а до 26.09.2026
                // именно протухший ключ давал этот 403 — за 20 часов 139
                // отказов на прогрессе, и место просмотра у ученика не
                // сохранялось до перезагрузки.
                var sendProgressRequest = window.csrfFetch || function (url, options) {
                    options.headers['X-CSRF-Token'] = csrfToken;
                    return fetch(url, options);
                };
                sendProgressRequest(data.progress_endpoint, {
                    method: 'POST',
                    credentials: 'same-origin',
                    keepalive: Boolean(keepalive),
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        position_seconds: progress.position_seconds,
                        duration_seconds: progress.duration_seconds,
                        playback_active: Boolean(progress.playback_active),
                        ended: Boolean(progress.ended),
                        session_id: sessionId,
                        // Только в разовой отметке о молчащем плеере
                        // (`reportPlayerFault`), в остальных поле опускается.
                        player_fault: progress.player_fault || undefined
                    })
                }).then(function (response) {
                    if (response.status === 401) { savingDisabled = true; throw new Error('session_expired'); }
                    if (response.status === 403) { savingDisabled = true; throw new Error('session_expired'); }
                    if (!response.ok) throw new Error('save_failed');
                    lastSavedSeconds = progress.position_seconds;
                    var recovered = saveFailures >= SAVE_FAILURES_BEFORE_ERROR;
                    saveFailures = 0;
                    if (recovered && !progress.completed) setStatus('Место просмотра сохранено.', false);
                    if (!hasReportedSaved && !progress.completed && !finishHintShown) {
                        hasReportedSaved = true;
                        setStatus('Место просмотра сохранено.', false);
                    }
                    return response.json().catch(function () { return null; });
                }).then(function (respData) {
                    if (respData && 'watch_map' in respData) renderWatchMap(respData.watch_map);
                    if (respData && respData.completed === true) {
                        setStatus(
                            onCompleted
                                ? 'Видео просмотрено. Можно отметить выполнение.'
                                : 'Видео просмотрено.',
                            false
                        );
                        if (!completionReported && onCompleted) onCompleted();
                        completionReported = true;
                    } else if (respData && respData.skipped === true && watchRequired
                        && !completionReported) {
                        // Сервер увидел перескок вперёд (`evaluate_watch`) —
                        // говорим сразу, а не когда ученик дойдёт до конца и
                        // упрётся в незасчитанный просмотр (владелец 06.10.2026).
                        setStatus('Перемотанный кусок не засчитается. Смотри без перемотки, ускорять можно до 2×.', true);
                    } else if (!progress.completed) {
                        return;
                    } else if (!watchRequired) {
                        setStatus('Место просмотра сохранено.', false);
                    } else {
                        setStatus('Просмотр пока не засчитан – часть ролика пропущена. Что досмотреть, видно под плеером.', true);
                    }
                }).catch(function (error) {
                    if (error.message === 'session_expired') setStatus('Обнови страницу, чтобы сохранять место просмотра.', true);
                    // Сохранение в конце ролика следом уже не повторится —
                    // его сбой показываем сразу.
                    else if (++saveFailures >= SAVE_FAILURES_BEFORE_ERROR || progress.completed) {
                        setStatus('Не удалось сохранить место. Видео продолжит работать.', true);
                    }
                }).finally(function () {
                    saveInFlight = false;
                    if (pendingProgress) {
                        var nextProgress = pendingProgress;
                        pendingProgress = null;
                        sendProgress(nextProgress, false);
                    }
                });
            }

            function saveProgress(force, completed, keepalive) {
                if (savingDisabled || currentSeconds === null || currentSeconds < 0) return;
                if (!force && Math.abs(currentSeconds - lastSavedSeconds) < 2) return;
                sendProgress({
                    position_seconds: Math.max(0, currentSeconds),
                    duration_seconds: durationSeconds && durationSeconds > 0 ? durationSeconds : null,
                    completed: Boolean(completed),
                    playback_active: Boolean(isPlaying),
                    ended: Boolean(completed)
                }, keepalive);
            }

            // Ролик пошёл: общий признак «играет», обложка прочь и серверная
            // точка отсчёта. Без неё короткий ролик успевал закончиться до
            // первого 10-секундного heartbeat и навсегда оставался с
            // watched_seconds=0. Точка при каждом возобновлении ещё и отсекает
            // время, проведённое на паузе.
            function markPlaying() {
                setPlaying(true);
                hasEverPlayed = true;
                hideCover();
                saveProgress(true, false, false);
            }

            // Событие `play` от Bunny доходит не всегда (прод 06.10.2026,
            // ученица id 229: два ролика целиком на 1×, все отметки с
            // `playback_active=false`, засчитано 3 секунды из 576 и 23 из 399,
            // кружок отказан трижды). Поэтому «играет» выводим и из самой
            // позиции: она ровно идёт вперёд не медленнее, чем позволяют
            // 2,25× плеера, `PLAYBACK_PROOF_MS` подряд. Перемотка ползунком —
            // один большой скачок, она серию обрывает, и защита от перемотки
            // не слабеет.
            var PLAYBACK_PROOF_MS = 2000;
            var advanceStartedAt = null;
            var lastTickSeconds = null;
            var lastTickAt = null;
            function resetAdvance() {
                advanceStartedAt = null;
                lastTickSeconds = null;
                lastTickAt = null;
            }
            function noteTick(seconds) {
                var now = Date.now();
                if (lastTickSeconds !== null) {
                    var step = seconds - lastTickSeconds;
                    var elapsed = (now - lastTickAt) / 1000;
                    var steady = step > 0 && elapsed > 0 && elapsed <= 3 && step <= elapsed * 2.25 + 0.5;
                    if (!steady) advanceStartedAt = null;
                    else if (advanceStartedAt === null) advanceStartedAt = lastTickAt;
                }
                lastTickSeconds = seconds;
                lastTickAt = now;
                return advanceStartedAt !== null && now - advanceStartedAt >= PLAYBACK_PROOF_MS;
            }

            // Обработчики событий плеера — общие для Player.js и запасного
            // канала Bunny (`bunnystream` ниже): правило зачёта одно.
            function handleTime(seconds, duration) {
                currentSeconds = seconds;
                if (duration !== null && duration > 0) durationSeconds = duration;
                if (noteTick(seconds) && !isPlaying) markPlaying();
                if (Date.now() - lastAutomaticSaveAt >= 10000) {
                    lastAutomaticSaveAt = Date.now();
                    saveProgress(false, false, false);
                }
            }
            function handlePause() {
                // Последний кусок до паузы тоже был просмотрен. Флаг активного
                // воспроизведения должен попасть в тело запроса до смены состояния.
                saveProgress(true, false, false);
                setPlaying(false);
                // Иначе первые тики после следующего «плей» сочтутся
                // продолжением старой серии.
                resetAdvance();
            }
            function handleEnded() {
                if (durationSeconds !== null) currentSeconds = durationSeconds;
                saveProgress(true, true, false);
                setPlaying(false);
            }

            // Разовая отметка о сбое плеера — серверу для статистики
            // (`video_watch_events.KIND_PLAYER`). Позиция та же, что в прошлой
            // отметке, поэтому на зачёт она не влияет.
            function reportPlayerFault(reason) {
                // Каждая причина — раз за страницу: `fallback` приходит
                // следом за `no_message` и должен дойти тоже.
                if (playerFaultsReported[reason]) return;
                playerFaultsReported[reason] = true;
                sendProgress({
                    position_seconds: Math.max(0, currentSeconds || 0),
                    duration_seconds: durationSeconds && durationSeconds > 0 ? durationSeconds : null,
                    completed: false,
                    playback_active: Boolean(isPlaying),
                    ended: false,
                    player_fault: reason
                }, false);
            }

            // Player.js принимает `ready`, только если адрес, который плеер
            // сообщает о себе (`location` внутри рамки), побуквенно равен
            // `iframe.src`, и отбрасывает всё, что пришло с другого домена.
            // Не совпало — подписки на события лежат в очереди вечно, без
            // единой ошибки. Поэтому слушаем сами и узнаём свою рамку по
            // `e.source`: это окно нашего iframe, его не подделать, в отличие
            // от адреса. Позицию из него сервер всё равно проверяет сам
            // (`evaluate_watch`, не быстрее 2,25×).
            window.addEventListener('message', function (event) {
                var player = activePlayer;
                if (!player || event.source !== iframe.contentWindow) return;
                var message = event.data;
                if (typeof message === 'string') {
                    try { message = JSON.parse(message); } catch (error) { return; }
                }
                if (!message || message.context !== 'player.js') return;
                if (message.event !== 'ready' || player.isReady || !message.value) return;
                var fault = event.origin !== player.origin
                    ? 'origin'
                    : (message.value.src !== iframe.src ? 'src' : null);
                // Совпало — `ready` библиотека примет сама.
                if (!fault) return;
                // Повторяем `Player.receive` без двух его проверок: `ready()`
                // отправляет накопленные подписки уже на верный домен, а
                // обработчики `ready` библиотека зовёт отдельно, следом.
                // Наш слушатель стоит раньше библиотечного, и тот потом
                // обработчики не повторит: подписка на `ready` одноразовая.
                player.origin = event.origin;
                player.loaded = true;
                player.ready(message);
                player.keeper.execute('ready', message.listener, message.value, player);
                reportPlayerFault(fault);
            });

            // Запасной канал Bunny (10.10.2026, ученица id 288: телефон,
            // приложение с экрана «Домой», без VPN). Видео играло три дня, а
            // Player.js не прислал ни одного события: 16 сеансов на 0:00,
            // засчитано 0 секунд, кружок отказан 39 раз. Player.js в рамке шлёт
            // события только на домен из своего `document.referrer` и
            // включается лишь на `loadeddata`; что из этого ломается на её
            // телефоне, с сервера не видно. У страницы плеера Bunny есть второй
            // канал: по команде `activate` она шлёт каждое событие с текущей
            // минутой (`channel: 'bunnystream'`) на любой домен — `'*'`. Слушаем
            // его, только пока Player.js не готов: тот остаётся главным.
            // `activate` страница плеера принимает лишь после разбора списка
            // качеств ролика, поэтому команду повторяем, пока не придёт ответ;
            // каждый повтор подписывает события ещё раз — дубли отсекает
            // `lastBunnyKey`. Позицию сервер проверяет тем же правилом.
            var BUNNY_ACTIVATE_AT_MS = [0, 1000, 3000, 6000, 10000, 20000, 40000, 70000];
            var bunnyLive = false;
            var lastBunnyKey = null;
            var bunnyResumePending = false;
            function postToFrame(command, parameter) {
                if (!iframe.contentWindow) return;
                var message = { command: command };
                if (parameter !== undefined) message.parameter = parameter;
                iframe.contentWindow.postMessage(message, '*');
            }
            function activateBunnyChannel(generation, player) {
                var step = 0;
                function next() {
                    if (generation !== playerGeneration || player.isReady || bunnyLive) return;
                    postToFrame('activate');
                    step += 1;
                    if (step < BUNNY_ACTIVATE_AT_MS.length) {
                        window.setTimeout(next, BUNNY_ACTIVATE_AT_MS[step] - BUNNY_ACTIVATE_AT_MS[step - 1]);
                    }
                }
                if (frameWrap.classList.contains('is-ready')) next();
                else iframe.addEventListener('load', next, { once: true });
            }
            // Переход на минуту: у запасного канала нет команды «перейти на»,
            // только «вперёд/назад на N секунд».
            function seekTo(target) {
                if (activePlayer && activePlayer.isReady) {
                    activePlayer.setCurrentTime(target);
                } else if (bunnyLive) {
                    var delta = target - currentSeconds;
                    if (delta > 0) postToFrame('forward', delta);
                    else if (delta < 0) postToFrame('rewind', -delta);
                } else {
                    return;
                }
                currentSeconds = target;
            }
            window.addEventListener('message', function (event) {
                var player = activePlayer;
                if (!player || player.isReady || event.source !== iframe.contentWindow) return;
                var message = event.data;
                if (!message || typeof message !== 'object' || message.channel !== 'bunnystream') return;
                var status = message.status || {};
                var seconds = finiteNumber(status.currentTime);
                var duration = finiteNumber(status.duration);
                var key = message.event + '|' + seconds + '|' + status.playing;
                if (key === lastBunnyKey) return;
                lastBunnyKey = key;
                if (!bunnyLive) {
                    bunnyLive = true;
                    if (playerSilent) {
                        playerSilent = false;
                        setStatus('Место просмотра сохраняется автоматически.', false);
                    }
                    // На iPhone Player.js включается только после «плей», и
                    // первым приходит этот канал. Сбоем считаем, если Player.js
                    // не ответил и через 10 секунд.
                    var generation = playerGeneration;
                    window.setTimeout(function () {
                        if (generation === playerGeneration && !player.isReady) reportPlayerFault('fallback');
                    }, 10000);
                }
                if (duration !== null && duration > 0) durationSeconds = duration;
                if (message.event === 'play' || message.event === 'playing') {
                    markPlaying();
                    if (bunnyResumePending) {
                        bunnyResumePending = false;
                        if (resumeSeconds >= 5 && (seconds === null || seconds < 1)) {
                            postToFrame('forward', resumeSeconds);
                            currentSeconds = resumeSeconds;
                            resetAdvance();
                            setStatus('Продолжили с ' + formatTime(resumeSeconds) + '.', false);
                        }
                    }
                } else if (message.event === 'timeupdate') {
                    if (seconds !== null) handleTime(seconds, duration);
                } else if (message.event === 'pause') {
                    handlePause();
                } else if (message.event === 'seeked') {
                    if (seconds !== null) currentSeconds = seconds;
                    saveProgress(true, false, false);
                } else if (message.event === 'ended') {
                    handleEnded();
                }
            });

            // Молчание отсчитывается, только пока плеер на экране и вкладка
            // открыта (09.10.2026). Плееры свёрнутых шагов и ушедшие за край
            // экрана `ready` не присылают, пока их не покажут: за первые сутки
            // после выкатки 940 сигналов «молчит» у 47 учеников — это все, кто
            // смотрел видео, и 80% сигналов пришли от уже засчитанных роликов
            // соседних шагов, пока ученик смотрел свой. Ученица id 219, ради
            // которой проверка заведена, смотрела ролик на экране — её случай
            // ловится по-прежнему.
            function watchSilence(generation, player) {
                var timer = null;
                var loaded = false;
                var visible = !('IntersectionObserver' in window);
                var observer = null;
                function stale() {
                    if (generation === playerGeneration && !player.isReady && !bunnyLive) return false;
                    if (timer) window.clearTimeout(timer);
                    if (observer) observer.disconnect();
                    return true;
                }
                function arm() {
                    if (timer) window.clearTimeout(timer);
                    timer = null;
                    if (stale() || !loaded || !visible) return;
                    timer = window.setTimeout(check, PLAYER_SILENCE_MS);
                }
                function check() {
                    timer = null;
                    if (stale()) return;
                    // Скрытая вкладка: браузер мог придержать и сам плеер.
                    if (document.visibilityState !== 'visible') { arm(); return; }
                    if (observer) observer.disconnect();
                    playerSilent = true;
                    showStatus('Просмотр сейчас не засчитывается: плеер не сообщает сайту, на какой ты минуте. Выключи VPN или открой кабинет в другом браузере и обнови страницу.', true);
                    reportPlayerFault('no_message');
                }
                if (!visible) {
                    observer = new IntersectionObserver(function (entries) {
                        visible = entries[entries.length - 1].isIntersecting;
                        arm();
                    });
                    observer.observe(frameWrap);
                }
                function start() { loaded = true; arm(); }
                // При перевыпуске ссылки плеер пересоздаётся уже после `load`.
                if (frameWrap.classList.contains('is-ready')) start();
                else iframe.addEventListener('load', start, { once: true });
            }

            function attachPlayer() {
                var generation = ++playerGeneration;
                var player = new window.playerjs.Player(iframe);
                activePlayer = player;
                resetAdvance();
                watchSilence(generation, player);
                // Новая рамка — новый запасной канал: подписка прежней ушла с ней.
                bunnyLive = false;
                lastBunnyKey = null;
                bunnyResumePending = resumeSeconds >= 5;
                activateBunnyChannel(generation, player);

                player.on('ready', function () {
                    if (generation !== playerGeneration) return;
                    // `ready` всё-таки дошёл — позже срока или после лечения
                    // по `e.source`: просмотр засчитывается, ссылку снова можно
                    // обновлять. Статус ниже перепишет `getDuration`.
                    playerSilent = false;
                    if (coverPlay && cover && cover.getAttribute('src')
                        && !frameWrap.classList.contains('is-started')) {
                        coverPlay.hidden = false;
                    }
                    frameWrap.classList.add('is-ready');
                    player.getDuration(function (value) {
                        durationSeconds = finiteNumber(value);
                        if (resumeSeconds >= 5) {
                            player.setCurrentTime(resumeSeconds);
                            currentSeconds = resumeSeconds;
                            setStatus('Продолжили с ' + formatTime(resumeSeconds) + '.', false);
                        } else {
                            currentSeconds = 0;
                            setStatus('Место просмотра сохраняется автоматически.', false);
                        }
                        // Только при первой загрузке: после переподключения
                        // плеера позиция уже своя, а после зачёта подсказка лжёт.
                        // Сервер поставил на первый пропущенный кусок
                        // (`video_progress.py::resume_gap`, 07.10.2026).
                        var gap = data.resume_gap;
                        if (gap && watchRequired && !finishHintShown && !completionReported) {
                            finishHintShown = true;
                            if (gap.parts <= 1 || gap.end - gap.start >= gap.missing) {
                                setStatus('Чтобы просмотр засчитался, досмотри кусок с '
                                    + formatTime(gap.start) + ' до ' + formatTime(gap.end) + '.', false);
                            } else {
                                setStatus('Чтобы просмотр засчитался, досмотри пропущенные куски – это '
                                    + (gap.missing < 60 ? 'меньше минуты' : Math.ceil(gap.missing / 60) + ' мин')
                                    + '. Первый начинается с ' + formatTime(gap.start) + '.', false);
                            }
                        }
                    });
                });
                player.on('timeupdate', function (raw) {
                    if (generation !== playerGeneration) return;
                    var timing = timingData(raw);
                    if (!timing || timing.seconds === null) return;
                    handleTime(timing.seconds, timing.duration);
                });
                player.on('play', function () {
                    if (generation !== playerGeneration) return;
                    markPlaying();
                });
                player.on('pause', function () {
                    if (generation !== playerGeneration) return;
                    handlePause();
                });
                player.on('seeked', function () {
                    if (generation !== playerGeneration) return;
                    player.getCurrentTime(function (value) {
                        var seconds = finiteNumber(value);
                        if (seconds !== null) currentSeconds = seconds;
                        saveProgress(true, false, false);
                    });
                });
                player.on('ended', function () {
                    if (generation !== playerGeneration) return;
                    handleEnded();
                });
            }

            iframe.src = data.player_url;
            attachPlayer();
            reattachPlayer = function () {
                resumeSeconds = currentSeconds;
                setPlaying(false);
                attachPlayer();
            };

            document.addEventListener('visibilitychange', function () {
                if (document.visibilityState === 'hidden') saveProgress(true, false, true);
            });
            window.addEventListener('pagehide', function () {
                saveProgress(true, false, true);
                playingPlayers.delete(playerKey);
            });
        }
    };
})();
