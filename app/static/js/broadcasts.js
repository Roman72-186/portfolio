// Экран «Рассылки» (staff_broadcasts*.html, владелец 07.10.2026).
//
// 1. Редактор текста в разметке Telegram: жирный, курсив, подчёркнутый,
//    зачёркнутый, спойлер, цитата, ссылка. Поле — contenteditable, перед
//    отправкой формы его HTML кладётся в скрытый textarea `text`; сервер
//    чистит его до разметки Telegram (`broadcasts.clean_telegram_html`), так
//    что здесь не нужно добиваться идеального HTML.
// 2. «Ученики поимённо» — поиск по списку со страницы и метки `.chip`, та же
//    разметка, что в «Доступности блока» конструктора. Код поиска там встроен
//    в шаблон конструктора и привязан к его строкам блоков, поэтому свой.
//    Вставленный список ников разбирается по одному с кнопкой «Добавить
//    найденных» (владелец 14.10.2026).
//    То же поле второй раз — «Кроме»: кого исключить (владелец 22.10.2026).
// 3. Живой счётчик «Получат N из M» — `GET /cabinet/staff/broadcasts/audience`.
// 4. Отправка форм со свежим ключом CSRF (`static/js/csrf.js`, правило в
//    AGENTS.md) и подтверждением, где оно нужно.
// 5. «Когда»: поле даты и времени видно только при отложенной отправке.
// 6. Проверка настройки до отправки формы (владелец 08.10.2026): ошибка
//    показывается на странице, форма не уходит — набранное не пропадает.
//    Правила — зеркало сервера: лимит текста (`broadcasts.text_problem`),
//    вложение (`_read_media`), время (`broadcasts.schedule_problem`).
// 7. Слот вложения (владелец 08.10.2026): сохранённое или только что
//    выбранное — миниатюрой, с «Заменить» и «Удалить»; кнопки выбора видны,
//    только пока слот пуст.
// 8. Кружок играет по нажатию (`data-bc-play`) — в слоте вложения, в «Как
//    увидят ученики», в журнале и в колокольчике ученика
//    (`cabinet_notifications.html` подключает этот файл ради него).
(function () {
    'use strict';

    function escapeHTML(value) {
        var div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    }

    // ── 4. Формы со свежим CSRF ────────────────────────────────────────────
    function submitWithFreshCsrf(form, button, busyText) {
        if (button) {
            button.disabled = true;
            if (busyText) button.textContent = busyText;
        }
        var go = function (token) {
            var field = form.querySelector('input[name="csrf_token"]');
            if (token && field) field.value = token;
            form.submit();
        };
        if (!window.csrfFresh) {
            go(null);
            return;
        }
        window.csrfFresh().then(go, function () { go(null); });
    }

    document.querySelectorAll('form[data-bc-fresh-csrf]').forEach(function (form) {
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var question = form.getAttribute('data-bc-confirm');
            if (question && !window.confirm(question)) return;
            submitWithFreshCsrf(form, form.querySelector('button[type="submit"]'), 'Минуту…');
        });
    });

    // ── 8. Кружок: нажатие — играть или пауза, «▶» виден, пока стоит ───────
    document.addEventListener('click', function (event) {
        var play = event.target.closest && event.target.closest('[data-bc-play]');
        if (!play) return;
        var video = play.querySelector('video');
        if (!video) return;
        if (video.paused) video.play(); else video.pause();
    });
    ['play', 'pause', 'ended'].forEach(function (name) {
        document.addEventListener(name, function (event) {
            var thumb = event.target.closest && event.target.closest('[data-bc-play]');
            if (thumb) thumb.classList.toggle('is-playing', !event.target.paused && !event.target.ended);
        }, true);
    });

    // Пока идёт отправка, отчёт обновляется сам.
    if (document.querySelector('[data-bc-refresh]')) {
        window.setTimeout(function () { window.location.reload(); }, 5000);
    }

    var form = document.querySelector('form[data-bc-form]');
    if (!form) return;

    // ── 1. Редактор ────────────────────────────────────────────────────────
    var editable = form.querySelector('[data-tg-editable]');
    var source = form.querySelector('[data-tg-source]');
    var counter = form.querySelector('[data-tg-counter]');
    var photoInput = form.querySelector('[data-bc-photo]');

    function closestIn(node, selector) {
        while (node && node !== editable) {
            if (node.nodeType === 1 && node.matches(selector)) return node;
            node = node.parentNode;
        }
        return null;
    }

    function unwrap(el) {
        var parent = el.parentNode;
        while (el.firstChild) parent.insertBefore(el.firstChild, el);
        parent.removeChild(el);
    }

    function selectionInEditor() {
        var sel = window.getSelection();
        if (!sel || !sel.rangeCount) return null;
        var range = sel.getRangeAt(0);
        if (!editable.contains(range.commonAncestorContainer)) return null;
        return range;
    }

    function toggleSpoiler() {
        var range = selectionInEditor();
        if (!range) return;
        var existing = closestIn(range.commonAncestorContainer, '.tg-spoiler');
        if (existing) {
            unwrap(existing);
            return;
        }
        if (range.collapsed) return;
        var span = document.createElement('span');
        span.className = 'tg-spoiler';
        try {
            range.surroundContents(span);
        } catch (e) {
            // Выделение пересекает границы тегов — обернём извлечённое.
            span.appendChild(range.extractContents());
            range.insertNode(span);
        }
    }

    function toggleQuote() {
        var range = selectionInEditor();
        if (!range) return;
        if (closestIn(range.commonAncestorContainer, 'blockquote')) {
            document.execCommand('formatBlock', false, 'div');
        } else {
            document.execCommand('formatBlock', false, 'blockquote');
        }
    }

    function addLink() {
        var range = selectionInEditor();
        if (!range || range.collapsed) {
            window.alert('Сначала выделите текст, который станет ссылкой.');
            return;
        }
        var url = window.prompt('Адрес ссылки (https://…)', 'https://');
        if (!url) return;
        url = url.trim();
        if (!/^https?:\/\//i.test(url)) {
            window.alert('Ссылка должна начинаться с https:// или http://');
            return;
        }
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        document.execCommand('createLink', false, url);
    }

    function clearFormatting() {
        var range = selectionInEditor();
        if (!range) return;
        document.execCommand('removeFormat');
        document.execCommand('unlink');
        editable.querySelectorAll('.tg-spoiler').forEach(function (span) {
            if (range.intersectsNode(span)) unwrap(span);
        });
    }

    form.querySelectorAll('[data-tg-cmd]').forEach(function (button) {
        // mousedown, а не click: иначе кнопка снимает выделение в редакторе.
        button.addEventListener('mousedown', function (event) {
            event.preventDefault();
            var cmd = button.getAttribute('data-tg-cmd');
            editable.focus();
            if (cmd === 'spoiler') toggleSpoiler();
            else if (cmd === 'quote') toggleQuote();
            else if (cmd === 'link') addLink();
            else if (cmd === 'clear') clearFormatting();
            else document.execCommand(cmd);
            updateCounter();
        });
        // С клавиатуры кнопка нажимается без mousedown.
        button.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            button.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
        });
    });

    editable.addEventListener('keydown', function (event) {
        if ((event.ctrlKey || event.metaKey) && (event.key === 'k' || event.key === 'K' || event.key === 'л')) {
            event.preventDefault();
            addLink();
        }
    });

    // Вставка — только текстом: чужое оформление из Word и браузера Telegram
    // не примет, а сервер его всё равно снимет — пусть человек видит то же.
    editable.addEventListener('paste', function (event) {
        event.preventDefault();
        var text = (event.clipboardData || window.clipboardData).getData('text/plain');
        document.execCommand('insertText', false, text);
    });

    // Сохранённое вложение помечено на удаление (раздел 7).
    function mediaRemoved() {
        var field = form.querySelector('[data-bc-remove-media]');
        return !!field && field.value === '1';
    }

    function currentMediaKind() {
        if (photoInput && photoInput.files && photoInput.files.length) return 'photo';
        var audio = form.querySelector('input[type=file][name="audio"]');
        if (audio && audio.files && audio.files.length) return 'voice';
        var video = form.querySelector('input[type=file][name="video"]');
        if (video && video.files && video.files.length) return 'video_note';
        if (mediaRemoved()) return '';
        return counter ? counter.getAttribute('data-media-kind') : '';
    }

    function textState() {
        var kind = currentMediaKind();
        var limit = (kind === 'photo' || kind === 'voice')
            ? Number(counter.getAttribute('data-limit-caption'))
            : Number(counter.getAttribute('data-limit-text'));
        return {limit: limit, length: (editable.innerText || '').replace(/\n$/, '').length};
    }

    function updateCounter() {
        if (!counter) return;
        var state = textState();
        var limit = state.limit;
        var length = state.length;
        counter.textContent = length + ' из ' + limit + ' символов'
            + (length > limit ? ' — сократите текст' : '');
        counter.classList.toggle('prg-badge--error', length > limit);
    }

    editable.addEventListener('input', updateCounter);
    form.addEventListener('change', function (event) {
        if (event.target && event.target.type === 'file') updateCounter();
    });
    updateCounter();

    // ── 2. Ученики поимённо и «Кроме» ──────────────────────────────────────
    // Два поля с одним поиском: кого добавить поимённо и кого исключить
    // (владелец 22.10.2026: «исключать по юзернейму»). Ученик не стоит в
    // обоих сразу: выбрали в одном — пропал из другого (сервер держит то же,
    // `broadcasts.normalize_audience`).
    var studentsData = [];
    var dataNode = document.getElementById('bc-students');
    if (dataNode) {
        try { studentsData = JSON.parse(dataNode.textContent || '[]'); } catch (e) { studentsData = []; }
    }
    var RESULTS_MAX = 8;

    // Вставили список ников (владелец 14.10.2026: 14 ников через пробел давали
    // «Никого не нашли» — вся строка искалась как один ученик). Список — это
    // два и больше слова с «@» или слова через запятую, точку с запятой или
    // перенос строки; «Иванов Иван» остаётся обычным поиском по имени.
    // Однострочное поле при вставке столбика склеивает строки в «@a@b» —
    // поэтому «@» тоже граница: в нике Telegram его не бывает.
    function listTokens(value) {
        var raw = (value || '').replace(/@+/g, ' @').trim();
        var tokens = raw.split(/[\s,;]+/).filter(Boolean);
        if (tokens.length < 2) return null;
        var withAt = tokens.filter(function (t) { return t.charAt(0) === '@'; }).length;
        return withAt >= 2 || /[,;\n]/.test(raw) ? tokens : null;
    }

    // Каждое слово списка — точный ник (без «@», ссылка t.me тоже годится)
    // или номер ученика. По части ника не ищем: из списка легко добавить
    // чужого человека с похожим ником.
    function matchToken(token) {
        var nick = token.toLowerCase().replace(/^https?:\/\/(www\.)?t\.me\//, '').replace(/^@+/, '');
        var queryId = studentIdFromQuery(nick);
        for (var i = 0; i < studentsData.length; i++) {
            var s = studentsData[i];
            if (queryId !== null ? Number(s.id) === queryId
                : (s.username || '').toLowerCase() === nick) return s;
        }
        return null;
    }

    var pickers = [];

    function makePicker(box) {
        var search = box.querySelector('[data-bc-student-search]');
        var results = box.querySelector('[data-bc-student-results]');
        var chips = box.querySelector('[data-bc-student-chips]');
        var idsField = box.querySelector('[data-bc-student-ids]');
        var picker = {idsField: idsField};

        function chosenIds() {
            return (idsField.value || '').split(',').filter(Boolean).map(Number);
        }

        function renderChips() {
            var byId = {};
            studentsData.forEach(function (s) { byId[s.id] = s; });
            chips.innerHTML = chosenIds().map(function (id) {
                var s = byId[id] || {id: id, name: 'Ученик ' + id, username: ''};
                var nick = s.username ? ' @' + s.username : '';
                return '<span class="chip" data-id="' + Number(s.id) + '">'
                    + escapeHTML(s.name + nick)
                    + '<button type="button" class="chip-x" data-bc-student-remove="' + Number(s.id) + '"'
                    + ' aria-label="Убрать ' + escapeHTML(s.name) + '">×</button></span>';
            }).join('');
        }

        function renderListResults(tokens) {
            var chosen = {};
            chosenIds().forEach(function (id) { chosen[id] = true; });
            var toAdd = [];
            var already = 0;
            var missing = [];
            tokens.forEach(function (token) {
                var s = matchToken(token);
                if (!s) missing.push(token);
                else if (chosen[s.id]) already += 1;
                else if (toAdd.indexOf(Number(s.id)) < 0) toAdd.push(Number(s.id));
            });
            var html = '<p class="prg-hint">В списке ' + tokens.length + '. Нашли: ' + toAdd.length
                + (already ? ', уже выбраны: ' + already : '') + '.</p>';
            if (toAdd.length) {
                html += '<button type="button" class="btn-outline" data-bc-student-pick-all="' + toAdd.join(',') + '">'
                    + 'Добавить найденных: ' + toAdd.length + '</button>';
            }
            if (missing.length) {
                html += '<p class="prg-hint">Не нашли: ' + escapeHTML(missing.join(' '))
                    + '. Ник в анкете может отличаться, или доступ у ученика закрыт — найдите их по фамилии.</p>';
            }
            results.innerHTML = html;
            results.hidden = false;
        }

        function renderResults() {
            var tokens = listTokens(search.value);
            if (tokens) {
                renderListResults(tokens);
                return;
            }
            var needle = (search.value || '').trim().toLowerCase().replace(/^@/, '');
            if (!needle) {
                results.hidden = true;
                results.innerHTML = '';
                return;
            }
            var chosen = {};
            chosenIds().forEach(function (id) { chosen[id] = true; });
            var queryId = studentIdFromQuery(needle);
            var found = studentsData.filter(function (s) {
                if (chosen[s.id]) return false;
                if (queryId !== null) return Number(s.id) === queryId;
                return s.name.toLowerCase().indexOf(needle) >= 0
                    || (s.username || '').toLowerCase().indexOf(needle) >= 0;
            }).slice(0, RESULTS_MAX);
            results.innerHTML = found.length
                ? found.map(function (s) {
                    var extra = [s.username ? '@' + s.username : '', s.tariff].filter(Boolean).join(' · ');
                    return '<button type="button" class="prg-blk-student-option" data-bc-student-pick="' + Number(s.id) + '">'
                        + escapeHTML(s.name)
                        + (extra ? '<span class="prg-hint">' + escapeHTML(extra) + '</span>' : '')
                        + '</button>';
                }).join('')
                : '<p class="prg-hint">Никого не нашли.</p>';
            results.hidden = false;
        }

        function setIds(ids) {
            idsField.value = ids.join(',');
            renderChips();
            scheduleSummary();
        }

        // Выбранного здесь убрать из другого поля.
        function claim(ids) {
            pickers.forEach(function (other) {
                if (other !== picker) other.drop(ids);
            });
        }

        picker.drop = function (ids) {
            var left = chosenIds().filter(function (id) { return ids.indexOf(id) < 0; });
            if (left.length !== chosenIds().length) setIds(left);
        };

        search.addEventListener('input', renderResults);
        // Enter в поиске не должен отправлять форму.
        search.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') event.preventDefault();
        });
        box.addEventListener('click', function (event) {
            var pick = event.target.closest('[data-bc-student-pick]');
            var remove = event.target.closest('[data-bc-student-remove]');
            var pickAll = event.target.closest('[data-bc-student-pick-all]');
            if (pickAll) {
                var all = chosenIds();
                var added = pickAll.getAttribute('data-bc-student-pick-all').split(',').map(Number);
                added.forEach(function (id) {
                    if (all.indexOf(id) < 0) all.push(id);
                });
                // В поле остаются только ненайденные — видно, кого искать руками.
                var tokens = listTokens(search.value) || [];
                search.value = tokens.filter(function (t) { return !matchToken(t); }).join(' ');
                claim(added);
                setIds(all);
                renderResults();
                search.focus();
            } else if (pick) {
                var id = Number(pick.getAttribute('data-bc-student-pick'));
                var ids = chosenIds();
                if (ids.indexOf(id) < 0) ids.push(id);
                search.value = '';
                claim([id]);
                setIds(ids);
                renderResults();
                search.focus();
            } else if (remove) {
                var gone = Number(remove.getAttribute('data-bc-student-remove'));
                setIds(chosenIds().filter(function (id) { return id !== gone; }));
            }
        });
        renderChips();
        return picker;
    }

    form.querySelectorAll('[data-bc-students]').forEach(function (box) {
        pickers.push(makePicker(box));
    });
    // Поимённо — первое поле, «Кроме» — второе (`data-bc-excluded`).
    var idsField = form.querySelector('[data-bc-students]:not([data-bc-excluded]) [data-bc-student-ids]');
    var excludedField = form.querySelector('[data-bc-excluded] [data-bc-student-ids]');

    // ── 3. Живой счётчик ───────────────────────────────────────────────────
    var summary = form.querySelector('[data-bc-summary]');
    var audienceUrl = form.getAttribute('data-audience-url');
    var summaryTimer = null;
    var eastOffset = Number(form.getAttribute('data-east-offset')) || 0;
    var summarySeq = 0;

    function scheduleSummary() {
        window.clearTimeout(summaryTimer);
        summaryTimer = window.setTimeout(loadSummary, 300);
    }

    function loadSummary() {
        var params = new URLSearchParams();
        form.querySelectorAll('input[name="tariffs"]:checked').forEach(function (el) { params.append('tariffs', el.value); });
        form.querySelectorAll('input[name="levels"]:checked').forEach(function (el) { params.append('levels', el.value); });
        params.set('students', idsField.value || '');
        if (excludedField) params.set('excluded', excludedField.value || '');
        var seq = ++summarySeq;
        summary.textContent = 'Считаю…';
        fetch(audienceUrl + '?' + params.toString(), {
            credentials: 'same-origin', headers: {'Accept': 'application/json'}
        }).then(function (resp) {
            if (!resp.ok) throw new Error('audience');
            return resp.json();
        }).then(function (data) {
            if (seq !== summarySeq) return;
            if (!data.total) {
                var chosenAny = params.getAll('tariffs').length || params.getAll('levels').length
                    || (idsField.value || '').length;
                summary.textContent = chosenAny
                    ? 'Под этот выбор не подходит ни один ученик.'
                    : 'Пока никого не выбрано.';
                return;
            }
            if (typeof data.east_offset === 'number') eastOffset = data.east_offset;
            var text = 'Получат ' + data.reachable + ' из ' + data.total + '.';
            if (data.no_telegram) text += ' Не подключили бота: ' + data.no_telegram + '.';
            if (data.notifications_off) text += ' Выключили уведомления: ' + data.notifications_off + '.';
            summary.textContent = text;
        }).catch(function () {
            if (seq === summarySeq) summary.textContent = 'Не удалось посчитать — сохраните, число появится после.';
        });
    }

    // ── 5. Когда: поле даты — только для отложенной отправки ───────────────
    var whenTime = form.querySelector('[data-bc-when-time]');
    function syncWhen() {
        var chosen = form.querySelector('input[data-bc-when]:checked');
        if (whenTime) whenTime.hidden = !(chosen && chosen.value);
    }

    form.addEventListener('change', function (event) {
        if (event.target && event.target.hasAttribute('data-bc-audience')) scheduleSummary();
        if (event.target && event.target.hasAttribute('data-bc-when')) syncWhen();
    });

    // ── 6. Проверка до отправки формы ──────────────────────────────────────
    var problemsBox = form.querySelector('[data-bc-problems]');
    var maxPhotoBytes = (Number(form.getAttribute('data-max-photo-mb')) || 0) * 1024 * 1024;
    var IMAGE_EXT = /\.(jpe?g|jpe|jfif|a?png|webp|hei[cf]|avif|gif|bmp|tiff?|svg)$/i;
    var LOCAL_MODE = 'local';  // SCHEDULE_LOCAL в models/broadcast.py
    var checkedAction = null;  // после первой попытки — проверять на лету

    function pickedFile(name) {
        var input = form.querySelector('input[type=file][name="' + name + '"]');
        return input && input.files && input.files.length ? input.files[0] : null;
    }

    function hasMessage() {
        if (textState().length) return true;
        if (pickedFile('photo') || pickedFile('audio') || pickedFile('video')) return true;
        return form.getAttribute('data-has-media') === '1' && !mediaRemoved();
    }

    // datetime-local по Москве → миллисекунды UTC.
    function mskMoment(value) {
        var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || '');
        if (!m) return null;
        return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4] - 3, +m[5]);
    }

    function problems(action) {
        var list = [];
        var state = textState();
        if (state.length > state.limit) {
            list.push('Текст длиннее ' + state.limit + ' символов: сейчас ' + state.length
                + (state.limit < Number(counter.getAttribute('data-limit-text'))
                    ? ' (подпись к фото и голосовому — до ' + state.limit + ')' : ''));
        }

        var photo = pickedFile('photo');
        if (photo && (pickedFile('audio') || pickedFile('video'))) {
            list.push('В сообщении может быть только одно вложение: фото, голосовое или кружок');
        }
        if (photo && !(/^image\//i.test(photo.type || '') || IMAGE_EXT.test(photo.name || ''))) {
            list.push('Фото должно быть в формате jpg, png, webp или heic');
        }
        if (photo && maxPhotoBytes && photo.size > maxPhotoBytes) {
            list.push('Фото больше ' + (maxPhotoBytes / 1024 / 1024) + ' МБ');
        }

        var when = form.querySelector('input[data-bc-when]:checked');
        var mode = when ? when.value : '';
        if (mode) {
            var timeInput = form.querySelector('input[name="scheduled_at"]');
            var moment = mskMoment(timeInput && timeInput.value);
            if (moment === null) {
                list.push('Укажите дату и время отправки');
            } else {
                var first = mode === LOCAL_MODE ? moment - eastOffset * 3600 * 1000 : moment;
                if (first <= Date.now()) {
                    list.push(first !== moment
                        ? 'У части учеников это время по их часам уже прошло — выберите позже'
                        : 'Это время уже прошло — выберите позже');
                }
            }
        }

        if (action === 'preview') {
            if (!hasMessage()) list.push('Сообщение пустое: добавьте текст или вложение');
            var chosenAny = form.querySelector('input[name="tariffs"]:checked')
                || form.querySelector('input[name="levels"]:checked')
                || (idsField.value || '').length;
            if (!chosenAny) list.push('Не выбрано, кому отправить');
        }
        return list;
    }

    // Показать ошибки; true — ошибок нет, форму можно отправлять.
    function showProblems(scroll) {
        if (!problemsBox || !checkedAction) return true;
        var list = problems(checkedAction);
        problemsBox.hidden = !list.length;
        problemsBox.innerHTML = list.length
            ? '<p>Не отправлено: поправьте и нажмите снова. Набранное на месте.</p><ul>'
                + list.map(function (item) { return '<li>' + escapeHTML(item) + '</li>'; }).join('')
                + '</ul>'
            : '';
        if (list.length && scroll) problemsBox.scrollIntoView({block: 'center', behavior: 'smooth'});
        return !list.length;
    }

    // Список ошибок сам укорачивается, пока человек их правит.
    form.addEventListener('input', function () { showProblems(false); });
    form.addEventListener('change', function () { showProblems(false); });

    // ── 7. Слот вложения ───────────────────────────────────────────────────
    // В слоте одно: новое (выбранный файл) главнее сохранённого. Удалить
    // сохранённое — пометка `remove_media=1`, сервер уберёт его при
    // «Сохранить»; до того «Вернуть» снимает пометку. Новое удаляется сразу:
    // голосовое и кружок — через сам компонент записи (`_mrf.clear`), фото —
    // очисткой поля. «Заменить» открывает кнопки выбора, у сохранённого — с
    // «Оставить прежнее».
    var attach = form.querySelector('[data-bc-attach]');
    if (attach) (function () {
        var savedCard = attach.querySelector('[data-bc-saved]');
        var pendingCard = attach.querySelector('[data-bc-pending]');
        var pendingThumb = attach.querySelector('[data-bc-pending-thumb]');
        var pendingTitle = attach.querySelector('[data-bc-pending-title]');
        var removedNote = attach.querySelector('[data-bc-removed]');
        var replacingNote = attach.querySelector('[data-bc-replacing]');
        var pickers = attach.querySelector('[data-bc-pickers]');
        var photoPick = attach.querySelector('[data-bc-photo-pick]');
        var recorderRoot = attach.querySelector('[data-media-recorder]');
        var removeField = attach.querySelector('[data-bc-remove-media]');
        var LABELS = {photo: 'Фото', voice: 'Голосовое', video_note: 'Кружок'};
        var replacing = false;
        var shownFile = null;
        var shownUrl = null;

        function pending() {
            var pairs = [['photo', 'photo'], ['audio', 'voice'], ['video', 'video_note']];
            for (var i = 0; i < pairs.length; i++) {
                var file = pickedFile(pairs[i][0]);
                if (file) return {kind: pairs[i][1], file: file};
            }
            return null;
        }

        // Компонент записи не в покое — идёт подготовка или запись.
        function recorderBusy() {
            var idle = recorderRoot && recorderRoot.querySelector('[data-mrf-idle]');
            return !!idle && idle.hidden;
        }

        // Та же разметка, что у сохранённого в шаблоне.
        function thumbNode(kind, url) {
            var node;
            if (kind === 'photo') {
                node = document.createElement('a');
                node.className = 'bc-thumb';
                node.href = url;
                node.target = '_blank';
                node.rel = 'noopener';
                node.setAttribute('aria-label', 'Открыть фото целиком');
                var img = document.createElement('img');
                img.src = url;
                img.alt = '';
                node.appendChild(img);
            } else if (kind === 'video_note') {
                node = document.createElement('button');
                node.type = 'button';
                node.className = 'bc-thumb bc-thumb--note';
                node.setAttribute('data-bc-play', '');
                node.setAttribute('aria-label', 'Посмотреть кружок');
                var video = document.createElement('video');
                video.setAttribute('playsinline', '');
                video.preload = 'metadata';
                video.src = url + '#t=0.1';
                var icon = document.createElement('span');
                icon.className = 'bc-thumb-play';
                icon.setAttribute('aria-hidden', 'true');
                icon.textContent = '▶';
                node.appendChild(video);
                node.appendChild(icon);
            } else {
                node = document.createElement('audio');
                node.className = 'bc-thumb-audio';
                node.controls = true;
                node.preload = 'metadata';
                node.src = url;
            }
            return node;
        }

        function showPending(item) {
            if (item && shownFile === item.file) return;
            if (shownUrl) URL.revokeObjectURL(shownUrl);
            shownUrl = null;
            shownFile = item ? item.file : null;
            pendingThumb.innerHTML = '';
            if (!item) return;
            shownUrl = URL.createObjectURL(item.file);
            pendingThumb.appendChild(thumbNode(item.kind, shownUrl));
            pendingTitle.textContent = LABELS[item.kind];
        }

        function sync() {
            var item = pending();
            var removed = removeField.value === '1';
            var savedShown = !!savedCard && !removed && !replacing && !item;
            showPending(item);
            if (savedCard) savedCard.hidden = !savedShown;
            pendingCard.hidden = !item;
            removedNote.hidden = !(savedCard && removed && !item);
            pickers.hidden = savedShown || !!item;
            photoPick.hidden = recorderBusy();
            if (replacingNote) replacingNote.hidden = !(replacing && !removed && !item && !recorderBusy());
        }

        function clearPending() {
            var item = pending();
            if (!item) return;
            if (item.kind === 'photo') {
                photoInput.value = '';
                photoInput.dispatchEvent(new Event('change', {bubbles: true}));
            } else if (recorderRoot && recorderRoot._mrf) {
                recorderRoot._mrf.clear();
            }
        }

        attach.addEventListener('click', function (event) {
            if (event.target.closest('[data-bc-attach-remove]')) {
                if (event.target.closest('[data-bc-saved]')) removeField.value = '1';
                else clearPending();
            } else if (event.target.closest('[data-bc-attach-replace]')) {
                clearPending();
                replacing = true;
            } else if (event.target.closest('[data-bc-attach-keep]')) {
                removeField.value = '';
                replacing = false;
            } else {
                return;
            }
            sync();
            updateCounter();
            showProblems(false);
        });

        form.addEventListener('change', sync);
        // Начало и конец записи событий не шлют — смотрим на смену стадий.
        if (recorderRoot && window.MutationObserver) {
            new MutationObserver(sync).observe(recorderRoot, {
                attributes: true, subtree: true, attributeFilter: ['hidden']
            });
        }
        sync();
    })();

    // ── Отправка формы черновика ───────────────────────────────────────────
    var actionField = form.querySelector('[data-bc-action]');
    var lastSubmitter = null;
    form.querySelectorAll('[data-bc-submit]').forEach(function (button) {
        button.addEventListener('click', function () { lastSubmitter = button; });
    });

    form.addEventListener('submit', function (event) {
        // Запись ещё идёт — отправку держит компонент записи (фаза захвата).
        if (event.defaultPrevented) return;
        if (form.querySelector('[data-mrf-busy]')) return;
        event.preventDefault();
        var submitter = event.submitter || lastSubmitter;
        var action = submitter ? submitter.getAttribute('data-bc-submit') : 'save';
        checkedAction = action || 'save';
        if (!showProblems(true)) return;
        // `form.submit()` не передаёт нажатую кнопку — действие идёт полем.
        actionField.value = action || 'save';
        source.value = editable.innerHTML;
        form.querySelectorAll('[data-bc-submit]').forEach(function (b) { b.disabled = true; });
        submitWithFreshCsrf(
            form, submitter,
            action === 'preview' ? 'Сохраняю и отправляю вам…' : 'Сохраняю…'
        );
    });
})();
