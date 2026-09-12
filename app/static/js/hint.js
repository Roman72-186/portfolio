(function () {
    var wraps = document.querySelectorAll('.hint-wrap');
    if (!wraps.length) return;

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

    function closeAll(except) {
        wraps.forEach(function (wrap) {
            if (wrap === except) return;
            var pop = wrap.querySelector('.hint-pop');
            var btn = wrap.querySelector('.hint-trigger');
            if (!pop.hidden) {
                pop.hidden = true;
                btn.setAttribute('aria-expanded', 'false');
            }
        });
    }

    wraps.forEach(function (wrap) {
        var btn = wrap.querySelector('.hint-trigger');
        var pop = wrap.querySelector('.hint-pop');
        var closeBtn = wrap.querySelector('.hint-pop-close');

        function openPop() {
            closeAll(wrap);
            pop.hidden = false;
            positionPop(btn, pop);
            btn.setAttribute('aria-expanded', 'true');
        }
        function closePop() {
            pop.hidden = true;
            btn.setAttribute('aria-expanded', 'false');
        }

        btn.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (pop.hidden) openPop(); else closePop();
        });
        if (closeBtn) {
            closeBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                closePop();
            });
        }
    });

    document.addEventListener('click', function (e) {
        wraps.forEach(function (wrap) {
            var pop = wrap.querySelector('.hint-pop');
            if (!pop.hidden && !wrap.contains(e.target)) {
                pop.hidden = true;
                wrap.querySelector('.hint-trigger').setAttribute('aria-expanded', 'false');
            }
        });
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeAll(null);
    });
})();
