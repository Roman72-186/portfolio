(function(){
    var lb = document.getElementById('lightbox');
    var img = document.getElementById('lightbox-img');
    var thumbs = document.getElementById('lightbox-thumbs');
    var counter = document.getElementById('lightbox-counter');
    var slides = [];
    var idx = 0;
    var lastFocusedEl = null;
    // Поворот: кнопки есть только в разметке ролей, которым он вообще положен,
    // а какие фото крутить можно, сервер отвечает на каждое открытие.
    var tools = document.getElementById('lightbox-tools');
    var rotatable = new Set();
    var rotatableSeq = 0;

    // Адрес без `?v=` и в одном написании. Сервер после поворота отдаёт путь
    // с кириллицей буквами («До», «После», тариф), а на странице тот же файл
    // записан через %D0%94…: без приведения это два разных фото, и кнопки
    // пропадали после первого поворота (владелец 06.10.2026).
    function baseUrl(u) {
        var clean = (u || '').split('?')[0];
        if (!clean) return '';
        try { return new URL(clean, location.href).href; } catch (_) { return clean; }
    }

    function updateTools() {
        if (!tools) return;
        var s = slides[idx];
        tools.hidden = !(s && rotatable.has(baseUrl(s.full)));
    }

    function checkRotatable() {
        if (!tools) return;
        rotatable = new Set();
        updateTools();
        var seq = ++rotatableSeq;
        var srcs = [];
        slides.forEach(function(s){
            var u = baseUrl(s.full);
            if (u && srcs.indexOf(u) < 0) srcs.push(u);
        });
        if (!srcs.length) return;
        fetch('/cabinet/rotate-photo/allowed', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': window.LIGHTBOX_CSRF || ''},
            body: JSON.stringify({srcs: srcs})
        }).then(function(r){ return r.ok ? r.json() : {allowed: []}; }).then(function(d){
            if (seq !== rotatableSeq) return;  // просмотрщик уже открыли на другом наборе
            rotatable = new Set((d && d.allowed) || []);
            updateTools();
        }).catch(function(){});
    }

    function render() {
        if (!slides.length) return;
        idx = (idx + slides.length) % slides.length;
        var s = slides[idx];
        img.src = s.full;
        img.alt = s.alt || '';
        counter.textContent = (idx + 1) + ' / ' + slides.length;
        var thumbEls = thumbs.querySelectorAll('.lightbox-thumb');
        thumbEls.forEach(function(el, i){
            el.classList.toggle('is-current', i === idx);
            if (i === idx) {
                var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                el.scrollIntoView({block:'nearest', inline:'center', behavior: reducedMotion ? 'auto' : 'smooth'});
            }
        });
        lb.classList.toggle('is-single', slides.length <= 1);
        updateTools();
        resetZoom();
    }

    function setSlides(list, startIdx) {
        slides = list || [];
        idx = Math.max(0, Math.min(startIdx || 0, slides.length - 1));
        thumbs.innerHTML = '';
        slides.forEach(function(s, i){
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'lightbox-thumb';
            btn.setAttribute('role', 'tab');
            btn.setAttribute('aria-label', 'Фото ' + (i + 1));
            btn.onclick = function(){ idx = i; render(); };
            var t = document.createElement('img');
            t.src = s.thumb || s.full;
            t.alt = '';
            t.loading = 'lazy';
            btn.appendChild(t);
            thumbs.appendChild(btn);
        });
        render();
        checkRotatable();
    }

    window.lightboxStep = function(delta){
        if (!slides.length) return;
        idx += delta;
        render();
    };

    // Поворот текущего фото на 90° (перезаписывает файл в S3). Ссылка без `?v=`
    // остаётся прежней, поэтому разрешение из `rotatable` действует и дальше.
    //
    // Занято, пока новое фото не нарисовалось, а не только пока ответил сервер:
    // на телефоне фото 1600 px грузится секунды, старое всё это время стоит на
    // экране, и ребёнок жал ⟳ второй раз — фото уходило на 180°.
    var rotating = false;
    function setBusy(on, btn) {
        rotating = on;
        if (!tools) return;
        tools.classList.toggle('is-busy', on);
        tools.setAttribute('aria-busy', on ? 'true' : 'false');
        tools.querySelectorAll('.lightbox-tool').forEach(function(b){
            b.classList.toggle('is-spinning', on && b === btn);
        });
    }
    function whenShown(done) {
        var called = false;
        function finish() {
            if (called) return;
            called = true;
            img.removeEventListener('load', finish);
            img.removeEventListener('error', finish);
            done();
        }
        img.addEventListener('load', finish);
        img.addEventListener('error', finish);
        // Сеть совсем плохая — не держим кнопки запертыми вечно.
        setTimeout(finish, 15000);
    }

    window.lightboxRotate = function(direction, btn){
        if (!slides.length || rotating) return;
        var s = slides[idx];
        var base = baseUrl(s.full);
        if (!base) return;
        setBusy(true, btn);
        var fd = new FormData();
        fd.append('src', base);
        fd.append('direction', direction);
        fetch('/cabinet/rotate-photo', {
            method: 'POST',
            headers: {'X-CSRF-Token': window.LIGHTBOX_CSRF || ''},
            body: fd
        }).then(function(r){ return r.json(); }).then(function(d){
            if (!d || !d.success) { setBusy(false); alert((d && d.error) || 'Не удалось повернуть фото'); return; }
            var newUrl = d.src;
            // Сервер только что повернул этот файл — значит, крутить его можно и дальше.
            rotatable.add(baseUrl(newUrl));
            // Превью работы сервер пересобрал; нет превью — квадратик берёт само фото.
            var newThumb = d.thumb_src || newUrl;
            // Обновляем все <img> на странице, указывающие на старый объект.
            document.querySelectorAll('img').forEach(function(im){
                if (baseUrl(im.getAttribute('src')) === base) im.src = newUrl;
                if (baseUrl(im.getAttribute('data-full')) === base) {
                    im.setAttribute('data-full', newUrl);
                    im.src = newThumb;
                }
                if (baseUrl(im.getAttribute('data-thumb')) === base) im.setAttribute('data-thumb', newUrl);
            });
            // Обновляем слайд и его миниатюру, перерисовываем.
            s.full = newUrl;
            s.thumb = newThumb;
            var thumbImg = thumbs.querySelectorAll('.lightbox-thumb img')[idx];
            if (thumbImg) thumbImg.src = newThumb;
            // Листнули на другое фото, пока сервер крутил, — ждать нечего.
            if (slides[idx] === s) whenShown(function(){ setBusy(false); });
            else setBusy(false);
            render();
        }).catch(function(){
            setBusy(false);
            alert('Ошибка сети при повороте фото');
        });
    };

    window.openLightbox = function(src, alt){
        if (!src) return;
        lastFocusedEl = document.activeElement;
        setSlides([{full: src, thumb: src, alt: alt || ''}], 0);
        lb.classList.add('open');
        lb.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        lb.querySelector('.lightbox-close').focus();
    };

    window.openLightboxGroup = function(list, startIdx){
        if (!list || !list.length) return;
        lastFocusedEl = document.activeElement;
        setSlides(list, startIdx || 0);
        lb.classList.add('open');
        lb.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        lb.querySelector('.lightbox-close').focus();
    };

    window.openGallery = function(el){
        if (!el) return;
        var root = el.closest('[data-gallery]') || el.parentElement;
        var imgs = Array.from(root.querySelectorAll('img'));
        if (!imgs.length) { window.openLightbox(el.src, el.alt); return; }
        var list = imgs.map(function(i){
            return {
                full: i.dataset.full || i.src,
                thumb: i.dataset.thumb || i.src,
                alt: i.alt || ''
            };
        });
        var start = imgs.indexOf(el);
        window.openLightboxGroup(list, start < 0 ? 0 : start);
    };

    window.closeLightbox = function(e){
        if (e && e.target && e.target.tagName === 'IMG' && e.target.id === 'lightbox-img') return;
        lb.classList.remove('open');
        lb.setAttribute('aria-hidden', 'true');
        resetZoom();
        document.body.style.overflow = '';
        if (lastFocusedEl && typeof lastFocusedEl.focus === 'function') lastFocusedEl.focus();
        lastFocusedEl = null;
    };

    document.addEventListener('keydown', function(e){
        if (!lb.classList.contains('open')) return;
        if (e.key === 'Tab') {
            var focusable = Array.from(lb.querySelectorAll('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'))
                .filter(function(el){ return el.offsetParent !== null; });
            if (!focusable.length) return;
            var first = focusable[0];
            var last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        } else if (e.key === 'Escape') window.closeLightbox();
        else if (e.key === 'ArrowLeft') window.lightboxStep(-1);
        else if (e.key === 'ArrowRight') window.lightboxStep(1);
        else if (e.key === '+' || e.key === '=') window.lightboxZoomBy(1.6);
        else if (e.key === '-') window.lightboxZoomBy(1 / 1.6);
        else if (e.key === '0') resetZoom();
    });

    // Приближение (владелец 07.10.2026: фото с правками в ОС нужно «рассмотреть,
    // сделать zoom»). Фото лежит в рамке `#lightbox-frame` и двигается
    // transform'ом внутри неё: `translate(tx, ty) scale(scale)` от центра.
    // Жесты — на Pointer Events, одним кодом для мыши, пальца и пера:
    //   два пальца — щипок, фото держится под пальцами;
    //   двойной тап или клик — 2.5× в точку касания, повторно — обратно;
    //   колесо мыши — в точку под курсором;
    //   один палец или мышь при приближении — перетащить фото;
    //   без приближения один палец по-прежнему листает свайпом.
    // `touch-action: none` на рамке (base.css) забирает жесты у браузера,
    // иначе щипок увеличивал бы всю страницу, а не фото.
    var frame = document.getElementById('lightbox-frame');
    var zoomInBtn = document.getElementById('lightbox-zoom-in');
    var zoomOutBtn = document.getElementById('lightbox-zoom-out');
    var MAX_SCALE = 5;
    var DOUBLE_TAP_SCALE = 2.5;
    var scale = 1, tx = 0, ty = 0;

    // Сдвиг ограничен так, чтобы край фото не отходил от края рамки.
    function clampPan() {
        var maxX = img.offsetWidth * (scale - 1) / 2;
        var maxY = img.offsetHeight * (scale - 1) / 2;
        tx = Math.max(-maxX, Math.min(maxX, tx));
        ty = Math.max(-maxY, Math.min(maxY, ty));
    }

    function applyZoom() {
        clampPan();
        img.style.transform = scale > 1 ? 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')' : '';
        lb.classList.toggle('is-zoomed', scale > 1);
        if (zoomOutBtn) zoomOutBtn.disabled = scale <= 1;
        if (zoomInBtn) zoomInBtn.disabled = scale >= MAX_SCALE;
    }

    function resetZoom() {
        scale = 1; tx = 0; ty = 0;
        applyZoom();
    }

    // Точка экрана в координатах от центра рамки.
    function framePoint(clientX, clientY) {
        var r = frame.getBoundingClientRect();
        return {x: clientX - (r.left + r.width / 2), y: clientY - (r.top + r.height / 2)};
    }

    // Новый масштаб, при котором точка `p` фото остаётся на месте.
    function zoomAt(p, next) {
        next = Math.max(1, Math.min(MAX_SCALE, next));
        if (next < 1.02) { resetZoom(); return; }
        tx = p.x - next * (p.x - tx) / scale;
        ty = p.y - next * (p.y - ty) / scale;
        scale = next;
        applyZoom();
    }

    window.lightboxZoomBy = function(factor){
        if (!lb.classList.contains('open')) return;
        zoomAt({x: 0, y: 0}, scale * factor);
    };

    var pointers = new Map();
    var gesture = null;
    var lastTap = null;

    function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
    function mid(a, b) { return {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2}; }

    function startGesture() {
        var pts = Array.from(pointers.values());
        if (pts.length >= 2) {
            var m = mid(pts[0], pts[1]);
            gesture = {type: 'pinch', d: dist(pts[0], pts[1]) || 1, m: framePoint(m.x, m.y),
                       scale: scale, tx: tx, ty: ty};
        } else if (pts.length === 1) {
            gesture = {type: 'one', x: pts[0].x, y: pts[0].y, tx: tx, ty: ty, moved: false, t: Date.now()};
        } else {
            gesture = null;
        }
        frame.classList.toggle('is-gesturing', !!gesture);
    }

    frame.addEventListener('pointerdown', function(e){
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        // Захват держит жест, даже если палец вышел за край рамки.
        try { frame.setPointerCapture(e.pointerId); } catch (_) {}
        pointers.set(e.pointerId, {x: e.clientX, y: e.clientY});
        startGesture();
    });

    frame.addEventListener('pointermove', function(e){
        if (!pointers.has(e.pointerId) || !gesture) return;
        pointers.set(e.pointerId, {x: e.clientX, y: e.clientY});
        var pts = Array.from(pointers.values());
        if (gesture.type === 'pinch' && pts.length >= 2) {
            var m = mid(pts[0], pts[1]);
            var p = framePoint(m.x, m.y);
            var next = Math.max(1, Math.min(MAX_SCALE, gesture.scale * dist(pts[0], pts[1]) / gesture.d));
            // Точка фото под пальцами в начале щипка остаётся под ними.
            tx = p.x - next * (gesture.m.x - gesture.tx) / gesture.scale;
            ty = p.y - next * (gesture.m.y - gesture.ty) / gesture.scale;
            scale = next;
            applyZoom();
        } else if (gesture.type === 'one') {
            var dx = e.clientX - gesture.x, dy = e.clientY - gesture.y;
            if (Math.abs(dx) > 6 || Math.abs(dy) > 6) gesture.moved = true;
            if (scale > 1) {
                tx = gesture.tx + dx;
                ty = gesture.ty + dy;
                applyZoom();
            }
        }
    });

    function endPointer(e){
        if (!pointers.has(e.pointerId)) return;
        var g = gesture;
        pointers.delete(e.pointerId);
        if (g && g.type === 'one' && e.type === 'pointerup') {
            var dx = e.clientX - g.x;
            if (scale <= 1 && g.moved && Math.abs(dx) > 40) {
                // Свайп листает, только пока фото не приближено.
                window.lightboxStep(dx < 0 ? 1 : -1);
                lastTap = null;
            } else if (!g.moved && Date.now() - g.t < 300) {
                var now = Date.now();
                if (lastTap && now - lastTap.t < 320 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 30) {
                    if (scale > 1) resetZoom();
                    else zoomAt(framePoint(e.clientX, e.clientY), DOUBLE_TAP_SCALE);
                    lastTap = null;
                } else {
                    lastTap = {t: now, x: e.clientX, y: e.clientY};
                }
            }
        }
        if (scale < 1.02 && scale !== 1) resetZoom();
        // Отпустили один палец из двух — дальше двигаем оставшимся, без рывка.
        startGesture();
    }
    frame.addEventListener('pointerup', endPointer);
    frame.addEventListener('pointercancel', endPointer);

    frame.addEventListener('wheel', function(e){
        if (!lb.classList.contains('open')) return;
        e.preventDefault();
        zoomAt(framePoint(e.clientX, e.clientY), scale * Math.exp(-e.deltaY * 0.0015));
    }, {passive: false});

    // Safari на iOS шлёт свои gesture-события в обход touch-action.
    frame.addEventListener('gesturestart', function(e){ e.preventDefault(); });

    // Новый файл (листание, поворот) — другие размеры, сдвиг пересчитываем.
    img.addEventListener('load', function(){ if (scale > 1) applyZoom(); });
})();
