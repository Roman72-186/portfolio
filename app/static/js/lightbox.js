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
    });

    // Swipe navigation on touch
    var touchStartX = null;
    img.addEventListener('touchstart', function(e){
        if (e.touches.length === 1) touchStartX = e.touches[0].clientX;
    }, {passive: true});
    img.addEventListener('touchend', function(e){
        if (touchStartX === null) return;
        var dx = (e.changedTouches[0] || {}).clientX - touchStartX;
        if (Math.abs(dx) > 40) window.lightboxStep(dx < 0 ? 1 : -1);
        touchStartX = null;
    });
})();
