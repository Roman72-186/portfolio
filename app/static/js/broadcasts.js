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
// 3. Живой счётчик «Получат N из M» — `GET /cabinet/staff/broadcasts/audience`.
// 4. Отправка форм со свежим ключом CSRF (`static/js/csrf.js`, правило в
//    AGENTS.md) и подтверждением, где оно нужно.
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

    function currentMediaKind() {
        if (photoInput && photoInput.files && photoInput.files.length) return 'photo';
        var audio = form.querySelector('input[type=file][name="audio"]');
        if (audio && audio.files && audio.files.length) return 'voice';
        var video = form.querySelector('input[type=file][name="video"]');
        if (video && video.files && video.files.length) return 'video_note';
        var remove = form.querySelector('input[name="remove_media"]');
        if (remove && remove.checked) return '';
        return counter ? counter.getAttribute('data-media-kind') : '';
    }

    function updateCounter() {
        if (!counter) return;
        var kind = currentMediaKind();
        var limit = (kind === 'photo' || kind === 'voice')
            ? Number(counter.getAttribute('data-limit-caption'))
            : Number(counter.getAttribute('data-limit-text'));
        var length = (editable.innerText || '').replace(/\n$/, '').length;
        counter.textContent = length + ' из ' + limit + ' символов'
            + (length > limit ? ' — сократите текст' : '');
        counter.classList.toggle('prg-badge--error', length > limit);
    }

    editable.addEventListener('input', updateCounter);
    var photoName = form.querySelector('[data-bc-photo-name]');
    form.addEventListener('change', function (event) {
        if (event.target && (event.target.type === 'file' || event.target.name === 'remove_media')) {
            updateCounter();
        }
        if (event.target === photoInput && photoName) {
            photoName.textContent = photoInput.files && photoInput.files.length ? photoInput.files[0].name : '';
        }
    });
    updateCounter();

    // ── 2. Ученики поимённо ────────────────────────────────────────────────
    var studentsData = [];
    var dataNode = document.getElementById('bc-students');
    if (dataNode) {
        try { studentsData = JSON.parse(dataNode.textContent || '[]'); } catch (e) { studentsData = []; }
    }
    var box = form.querySelector('[data-bc-students]');
    var search = box.querySelector('[data-bc-student-search]');
    var results = box.querySelector('[data-bc-student-results]');
    var chips = box.querySelector('[data-bc-student-chips]');
    var idsField = box.querySelector('[data-bc-student-ids]');
    var RESULTS_MAX = 8;

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

    function renderResults() {
        var needle = (search.value || '').trim().toLowerCase().replace(/^@/, '');
        if (!needle) {
            results.hidden = true;
            results.innerHTML = '';
            return;
        }
        var chosen = {};
        chosenIds().forEach(function (id) { chosen[id] = true; });
        var found = studentsData.filter(function (s) {
            if (chosen[s.id]) return false;
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

    search.addEventListener('input', renderResults);
    // Enter в поиске не должен отправлять форму.
    search.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') event.preventDefault();
    });
    box.addEventListener('click', function (event) {
        var pick = event.target.closest('[data-bc-student-pick]');
        var remove = event.target.closest('[data-bc-student-remove]');
        if (pick) {
            var id = Number(pick.getAttribute('data-bc-student-pick'));
            var ids = chosenIds();
            if (ids.indexOf(id) < 0) ids.push(id);
            search.value = '';
            setIds(ids);
            renderResults();
            search.focus();
        } else if (remove) {
            var gone = Number(remove.getAttribute('data-bc-student-remove'));
            setIds(chosenIds().filter(function (id) { return id !== gone; }));
        }
    });
    renderChips();

    // ── 3. Живой счётчик ───────────────────────────────────────────────────
    var summary = form.querySelector('[data-bc-summary]');
    var audienceUrl = form.getAttribute('data-audience-url');
    var summaryTimer = null;
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
            var text = 'Получат ' + data.reachable + ' из ' + data.total + '.';
            if (data.no_telegram) text += ' Не подключили бота: ' + data.no_telegram + '.';
            if (data.notifications_off) text += ' Выключили уведомления: ' + data.notifications_off + '.';
            summary.textContent = text;
        }).catch(function () {
            if (seq === summarySeq) summary.textContent = 'Не удалось посчитать — сохраните, число появится после.';
        });
    }

    form.addEventListener('change', function (event) {
        if (event.target && event.target.hasAttribute('data-bc-audience')) scheduleSummary();
    });

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
