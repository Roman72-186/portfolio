(function () {
    // Ключ — атрибутом тега в `base.html`: файл кэшируется, а ключ у каждой
    // сессии свой. Читать сразу: внутри DOMContentLoaded `currentScript` уже null.
    var script = document.currentScript;
    var csrfToken = script ? script.getAttribute('data-csrf-token') : '';
    // DOMContentLoaded, а не сразу: у ученика на мобилке вторая кнопка-
    // колокольчик рендерится лепестком в partials/bottom_nav.html, ниже по
    // странице (тот partial подключается из блока content, а этот script —
    // выше него) — querySelectorAll здесь и сейчас нашёл бы только первую
    // кнопку из .floating-widget-tray. Тот же приём, что у переключателя
    // темы в `base.html`.
    document.addEventListener('DOMContentLoaded', function(){
        // [data-notif-bell] — не единственная кнопка, обе открывают один и
        // тот же #notifPop — делегирование, а не getElementById.
        var bells = document.querySelectorAll('[data-notif-bell]');
        var pop = document.getElementById('notifPop');
        var body = document.getElementById('notifPopBody');
        var badges = document.querySelectorAll('[data-notif-bell-badge]');
        var closeBtn = document.getElementById('notifPopClose');
        if (!bells.length || !pop || !body) return;
        var CSRF = csrfToken;
        var loadId = 0;

        // textContent → innerHTML кавычек не экранирует, а результат идёт и в
        // `href="…"` (код-ревью 12.10.2026, P3): кавычки — сами.
        function esc(s){
            var d = document.createElement('div');
            d.textContent = (s == null ? '' : String(s));
            return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        }
        function clearBadge(){
            badges.forEach(function(badge){ badge.hidden = true; badge.textContent = '0'; });
            bells.forEach(function(bell){ bell.setAttribute('aria-label', 'Уведомления'); });
        }
        function render(items){
            if (!items || !items.length){
                body.innerHTML = '<div class="notif-pop-empty">Уведомлений пока нет</div>';
                return;
            }
            var html = '';
            items.forEach(function(n){
                var cls = 'notif-pop-item' + (n.is_read ? '' : ' unread');
                var inner =
                    '<div class="notif-pop-item-title">' + esc(n.title) + '</div>' +
                    (n.text ? '<div class="notif-pop-item-text">' + esc(n.text) + '</div>' : '') +
                    '<div class="notif-pop-item-meta">' + esc(n.created_at) + '</div>';
                if (n.href){
                    html += '<a href="' + esc(n.href) + '" class="' + cls + '">' + inner + '</a>';
                } else {
                    html += '<div class="' + cls + '">' + inner + '</div>';
                }
            });
            body.innerHTML = html;
        }
        function markRead(id){
            window.fetchWithTimeout('/cabinet/notifications/mark-read', {
                method: 'POST',
                headers: {'X-CSRF-Token': CSRF, 'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'csrf_token=' + encodeURIComponent(CSRF),
                cache: 'no-store'
            }).then(function(r){
                if (!r.ok || r.redirected) return;
                return r.json().then(function(data){
                    if (id === loadId && data.ok === true) clearBadge();
                });
            }).catch(function(){});
        }
        function load(){
            var id = ++loadId;
            body.innerHTML = '<div class="notif-pop-empty" role="status">Загрузка…</div>';
            window.fetchWithTimeout('/cabinet/notifications/feed', {headers:{'Accept':'application/json'}, cache:'no-store'})
                .then(function(r){
                    if (!r.ok || r.redirected) throw new Error();
                    return r.json();
                })
                .then(function(j){
                    if (id !== loadId) return;
                    if (!j || !Array.isArray(j.notifications)) throw new Error();
                    render(j.notifications);
                    if (j.unread_count > 0) markRead(id); else clearBadge();
                })
                .catch(function(){
                    if (id !== loadId) return;
                    body.innerHTML = '<div class="notif-pop-empty"><p role="status">Не удалось загрузить уведомления.</p>' +
                        '<button type="button" class="btn-outline">Повторить</button></div>';
                    body.querySelector('button').addEventListener('click', function(e){
                        e.stopPropagation();
                        load();
                    });
                });
        }
        function open(){
            pop.hidden = false;
            bells.forEach(function(bell){ bell.setAttribute('aria-expanded', 'true'); });
            load();
        }
        function close(){
            ++loadId;
            pop.hidden = true;
            bells.forEach(function(bell){ bell.setAttribute('aria-expanded', 'false'); });
        }
        bells.forEach(function(bell){
            bell.addEventListener('click', function(e){
                e.preventDefault();
                if (pop.hidden) open(); else close();
            });
        });
        if (closeBtn) closeBtn.addEventListener('click', close);
        document.addEventListener('click', function(e){
            if (pop.hidden) return;
            var insideBell = Array.prototype.some.call(bells, function(bell){ return bell.contains(e.target); });
            if (!pop.contains(e.target) && !insideBell) close();
        });
        document.addEventListener('keydown', function(e){
            if (e.key === 'Escape' && !pop.hidden) close();
        });
    });
})();
