// Подсказки «?» (`components/hint.html`; в конструкторе программы ту же
// разметку собирает `hintHTML` в `program_blocks_editor_js.html`).
//
// Один обработчик на документ, а не по обработчику на подсказку (06.10.2026):
// блоки конструктора рисуются на лету, и подсказка, появившаяся после загрузки
// страницы, иначе не открывалась бы вовсе.
(function () {
    function positionPop(btn, pop) {
        // Высота подсказки зависит от длины текста (в отличие от
        // .notif-gear-pop с фиксированной высотой) — меряем реальную после
        // pop.hidden = false, а не гадаем числом, иначе длинная подсказка
        // внизу невысокого экрана уедет вверх по неверной оценке и всё равно
        // вылезет за край.
        var r = btn.getBoundingClientRect();
        var popWidth = Math.min(260, window.innerWidth - 32);
        var left = Math.min(r.left, window.innerWidth - 16 - popWidth);
        left = Math.max(16, left);
        var popHeight = pop.offsetHeight;
        var top = r.bottom + 8;
        if (top + popHeight > window.innerHeight) top = Math.max(16, r.top - 8 - popHeight);
        pop.style.left = left + 'px';
        pop.style.top = top + 'px';
    }

    function setOpen(wrap, open) {
        var pop = wrap.querySelector('.hint-pop');
        var btn = wrap.querySelector('.hint-trigger');
        pop.hidden = !open;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) positionPop(btn, pop);
    }

    function closeAll(except) {
        document.querySelectorAll('.hint-wrap').forEach(function (wrap) {
            if (wrap === except) return;
            if (!wrap.querySelector('.hint-pop').hidden) setOpen(wrap, false);
        });
    }

    document.addEventListener('click', function (e) {
        var wrap = e.target.closest && e.target.closest('.hint-wrap');
        if (!wrap) {
            closeAll(null);
            return;
        }
        // Подсказка бывает внутри <summary> свёрнутой панели конструктора:
        // без этого клик по «?» или по тексту подсказки сворачивал бы панель.
        e.preventDefault();
        if (e.target.closest('.hint-pop-close')) {
            setOpen(wrap, false);
        } else if (e.target.closest('.hint-trigger')) {
            var willOpen = wrap.querySelector('.hint-pop').hidden;
            closeAll(wrap);
            setOpen(wrap, willOpen);
        }
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeAll(null);
    });
})();
