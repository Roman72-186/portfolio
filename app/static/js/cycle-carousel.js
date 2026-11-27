// Карусель циклов на АОП (`.lrn-cycles` в `cabinet_learning.html`) крутится
// по кругу в обе стороны (владелец 06.10.2026: «чтобы можно было скролить и
// влево, и вправо по кругу, чтобы было зацикливание кнопок»).
//
// Приём: по копии всего ряда слева и справа от настоящего. Когда прокрутка
// уходит в копию дальше чем на полряда, позиция сдвигается ровно на ширину
// ряда — под пальцем та же картинка, а впереди снова есть куда крутить.
// Копии — только для глаз: `aria-hidden`, без фокуса; сервер отдаёт ряд
// один раз, и тесты шаблона видят его как прежде.
//
// Student rows keep one card per week and wrap only when they overflow.
(function () {
    var strip = document.querySelector('.lrn-cycles');
    if (!strip) return;

    var originals = Array.prototype.slice.call(strip.children);
    var uniqueWeeks = strip.hasAttribute('data-loop-weeks');
    var looping = false;
    var period = 0;  // ширина одного ряда вместе с промежутком до копии

    function offsetIn(el) {
        return el.getBoundingClientRect().left - strip.getBoundingClientRect().left + strip.scrollLeft;
    }

    // Позиция, сдвинутая на целое число рядов внутрь среднего: под пальцем
    // та же картинка, а с обеих сторон есть куда крутить.
    function wrap(left) {
        if (!looping || period <= 0) return left;
        while (left < period / 2) left += period;
        while (left > period * 1.5) left -= period;
        return left;
    }

    function cloneAll() {
        return originals.map(function (el) {
            var copy = el.cloneNode(true);
            copy.setAttribute('aria-hidden', 'true');
            copy.setAttribute('tabindex', '-1');
            copy.removeAttribute('aria-current');
            copy.setAttribute('data-clone', '');
            return copy;
        });
    }

    function centerCurrent() {
        var current = strip.querySelector('.is-current:not([data-clone])');
        if (!current) return;
        var width = current.getBoundingClientRect().width;
        strip.scrollLeft = wrap(offsetIn(current) + width / 2 - strip.clientWidth / 2);
    }

    function startLoop() {
        var before = cloneAll();
        var after = cloneAll();
        before.forEach(function (el) { strip.insertBefore(el, originals[0]); });
        after.forEach(function (el) { strip.appendChild(el); });
        period = offsetIn(after[0]) - offsetIn(originals[0]);
        looping = true;
        strip.scrollLeft = period;
        centerCurrent();
    }

    function stopLoop() {
        Array.prototype.slice.call(strip.querySelectorAll('[data-clone]')).forEach(function (el) {
            el.remove();
        });
        looping = false;
        centerCurrent();
    }

    function sync() {
        if (uniqueWeeks) return;
        if (!looping && strip.scrollWidth > strip.clientWidth + 1) startLoop();
        else if (looping && period <= strip.clientWidth) stopLoop();
    }

    strip.addEventListener('scroll', function () {
        if (!looping) return;
        var left = wrap(strip.scrollLeft);
        if (left !== strip.scrollLeft) strip.scrollLeft = left;
    }, {passive: true});

    var resizeTimer = null;
    window.addEventListener('resize', function () {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(sync, 150);
    });

    // Перетаскивание мышью. Палец и тачпад крутят ряд сами. Сдвиг больше
    // пяти пикселей — это протяжка, а не нажатие: клик по кнопке, на которой
    // отпустили мышь, гасится, иначе протяжка открывала бы чужой цикл.
    function wrapAtEdge(start, delta) {
        var max = strip.scrollWidth - strip.clientWidth;
        if (!uniqueWeeks || max <= 1) return false;
        if (start <= 1 && delta < 0) strip.scrollLeft = max;
        else if (start >= max - 1 && delta > 0) strip.scrollLeft = 0;
        else return false;
        return true;
    }

    var touchStart = null;
    if (uniqueWeeks) {
        strip.addEventListener('touchstart', function (event) {
            var touch = event.touches[0];
            touchStart = {x: touch.clientX, y: touch.clientY, scroll: strip.scrollLeft};
        }, {passive: true});
        strip.addEventListener('touchend', function (event) {
            if (!touchStart) return;
            var touch = event.changedTouches[0];
            var dx = touchStart.x - touch.clientX;
            if (Math.abs(dx) > Math.max(24, Math.abs(touchStart.y - touch.clientY))) {
                wrapAtEdge(touchStart.scroll, dx);
            }
            touchStart = null;
        }, {passive: true});
        var lastWheel = -Infinity;
        strip.addEventListener('wheel', function (event) {
            if (Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
            var newGesture = event.timeStamp - lastWheel > 180;
            lastWheel = event.timeStamp;
            if (newGesture && wrapAtEdge(strip.scrollLeft, event.deltaX)) event.preventDefault();
        }, {passive: false});
    }

    var drag = null;
    var dragged = false;
    strip.addEventListener('pointerdown', function (e) {
        if (e.pointerType !== 'mouse' || e.button !== 0) return;
        drag = {x: e.clientX, scroll: strip.scrollLeft};
        dragged = false;
    });
    window.addEventListener('pointermove', function (e) {
        if (!drag) return;
        var dx = e.clientX - drag.x;
        if (!dragged && Math.abs(dx) > 5) dragged = true;
        if (!dragged) return;
        // Перескок на копию считается здесь же, а не в обработчике scroll
        // (тот приходит кадром позже): точка отсчёта сдвигается вместе с
        // позицией, иначе ряд дёрнется обратно.
        var target = drag.scroll - dx;
        var left = wrap(target);
        drag.scroll += left - target;
        strip.scrollLeft = left;
    });
    window.addEventListener('pointerup', function (event) {
        if (drag && dragged) wrapAtEdge(drag.scroll, drag.x - event.clientX);
        drag = null;
    });
    strip.addEventListener('click', function (e) {
        if (!dragged) return;
        dragged = false;
        e.preventDefault();
        e.stopPropagation();
    }, true);
    // Ссылку браузер сам таскает «картинкой» — это мешает протяжке.
    strip.addEventListener('dragstart', function (e) { e.preventDefault(); });

    sync();
    if (!looping) centerCurrent();
})();
