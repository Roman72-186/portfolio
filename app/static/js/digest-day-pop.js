/*
Окно дня в календаре дайджеста у ученика (владелец 05.10.2026: «будем
показывать что не одно событие и при нажатии показывать небольшое
всплывающее окно»). Тап по числу с событиями — небольшое окно у этой даты со
всеми событиями дня: цвет типа, название, тип и время, «Подключиться», если
у события есть ссылка.

Разметка — partials/digest_list.html: клетки `[data-day-pop]`, одно окно
`[data-day-pop-box]` на календарь, данные — JSON `#digestDayEvents`
(`tracker.digest_day_events`). Вид окна — общий `.hint-pop` из base.css,
позиционирование то же, что у подсказок (static/js/hint.js): fixed у
триггера, ниже него, а если не влезает — выше. Свой файл, а не hint.js: там
триггер — кнопка «?» внутри `.hint-wrap`, а здесь — клетка календаря и
содержимое собирается на лету.

Текст событий пишут люди — в окно он попадает только через textContent.
*/
(function () {
    var box = document.querySelector('[data-day-pop-box]');
    var dataNode = document.getElementById('digestDayEvents');
    if (!box || !dataNode) return;
    var byDay = JSON.parse(dataNode.textContent || '{}');
    // Окно — прямой потомок body: внутри вкладки трекера оно оказывалось в
    // чужом контексте наложения, и нижнее меню ученика (z-index 100)
    // закрывало низ окна, хотя у самого окна z-index 260.
    document.body.appendChild(box);
    var title = box.querySelector('.dgst-pop-title');
    var list = box.querySelector('.dgst-pop-list');
    var dayFormat = new Intl.DateTimeFormat('ru-RU', {day: 'numeric', month: 'long', weekday: 'long'});
    var opener = null;

    function parseIso(iso) {
        var parts = iso.split('-');
        return new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    }

    function position(cell) {
        var r = cell.getBoundingClientRect();
        var width = Math.min(260, window.innerWidth - 32);
        var left = Math.max(16, Math.min(r.left + r.width / 2 - width / 2, window.innerWidth - 16 - width));
        var top = r.bottom + 6;
        var height = box.offsetHeight;
        if (top + height > window.innerHeight - 8) top = Math.max(16, r.top - 6 - height);
        box.style.left = left + 'px';
        box.style.top = top + 'px';
    }

    function item(event) {
        var li = document.createElement('li');
        li.className = 'dgst-pop-item';
        var swatch = document.createElement('span');
        swatch.className = 'dgst-swatch dgst-color--' + event.color + (event.style === 'ring' ? ' is-ring' : '');
        swatch.setAttribute('aria-hidden', 'true');
        var name = document.createElement('span');
        name.className = 'dgst-pop-name';
        name.textContent = event.title;
        var meta = document.createElement('span');
        meta.className = 'dgst-pop-meta';
        meta.textContent = [event.type, event.time, event.dates].filter(Boolean).join(' · ');
        li.appendChild(swatch);
        li.appendChild(name);
        li.appendChild(meta);
        if (event.url) {
            var link = document.createElement('a');
            link.className = 'dgst-pop-link';
            link.href = event.url;
            link.target = '_blank';
            link.rel = 'noopener';
            link.textContent = 'Подключиться';
            li.appendChild(link);
        }
        return li;
    }

    function close() {
        if (box.hidden) return;
        box.hidden = true;
        if (opener) {
            opener.setAttribute('aria-expanded', 'false');
            opener.classList.remove('is-open');
        }
        opener = null;
    }

    function open(cell) {
        var events = byDay[cell.dataset.day] || [];
        if (!events.length) return;
        close();
        title.textContent = dayFormat.format(parseIso(cell.dataset.day));
        list.textContent = '';
        events.forEach(function (event) { list.appendChild(item(event)); });
        box.hidden = false;
        position(cell);
        opener = cell;
        cell.setAttribute('aria-expanded', 'true');
        cell.classList.add('is-open');
        box.querySelector('.hint-pop-close').focus({preventScroll: true});
    }

    document.addEventListener('click', function (e) {
        var cell = e.target.closest('[data-day-pop]');
        if (cell) {
            if (cell === opener) close(); else open(cell);
            return;
        }
        if (e.target.closest('.hint-pop-close') && box.contains(e.target)) {
            var back = opener;
            close();
            if (back) back.focus({preventScroll: true});
            return;
        }
        if (!box.contains(e.target)) close();
    });
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || box.hidden) return;
        var back = opener;
        close();
        if (back) back.focus({preventScroll: true});
    });
    // Окно fixed у даты: прокрутка или поворот экрана увели бы его от числа.
    window.addEventListener('scroll', close, {passive: true});
    window.addEventListener('resize', close);
})();
