/* Мобильное бургер-меню всех ролей (стиль — static/css/bottom_nav_burger.css).

   Вынесено 08.10.2026 из partials/bottom_nav.html: тем же списком теперь
   пользуются куратор (_curator_nav.html) и модератор/ГП/суперадмин
   (partials/staff_nav.html) вместо прежней пилюли из иконок (владелец
   08.10.2026: «у всех ролей… как у ученика»). Меню находим по атрибуту
   `data-burger-nav`, а не по id — партиалы ролей разные. Правишь файл —
   поднимай `?v=` во всех трёх партиалах. */
(function () {
    function init(nav) {
        var toggle = nav.querySelector('.burger-toggle');
        var backdrop = nav.querySelector('.burger-backdrop');
        var items = Array.prototype.slice.call(nav.querySelectorAll('.burger-item'));
        var loader = document.getElementById('nav-loader');
        if (!toggle) return;

        function setOpen(open) {
            nav.classList.toggle('open', open);
            toggle.setAttribute('aria-expanded', String(open));
            toggle.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
            items.forEach(function (link) {
                if (!link.classList.contains('nav-disabled')) {
                    link.tabIndex = open ? 0 : -1;
                }
            });
            // Фокус на первый пункт при открытии сюда не переносим (раньше
            // переносили у веера): programmatic focus() браузер на мобилке
            // засчитывает за focus-visible и рисует постоянное фиолетовое
            // кольцо на первом пункте при каждом открытии меню тапом —
            // воспроизведено 04.09.2026 (владелец прислал скриншот). Таб после
            // открытия уже ведёт в пункты списка (tabindex выше).
            if (!open) toggle.focus();
        }

        toggle.addEventListener('click', function () {
            setOpen(!nav.classList.contains('open'));
        });
        if (backdrop) backdrop.addEventListener('click', function () { setOpen(false); });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && nav.classList.contains('open')) setOpen(false);
        });
        nav.addEventListener('click', function (e) {
            var link = e.target.closest('.burger-item');
            if (!link) return;
            // Тема и колокольчик — пункты списка, но не переход на экран: своя
            // логика клика уже навешана в base.html (тема) и static/js/notif-bell.js
            // (колокольчик), здесь только закрываем меню следом.
            if (link.hasAttribute('data-theme-toggle-btn') || link.hasAttribute('data-notif-bell')) {
                setOpen(false);
                return;
            }
            if (link.classList.contains('nav-disabled') || link.classList.contains('burger-item-active')) return;
            // Гейт «Портфолио «До»» есть только у ученика (конец partials/bottom_nav.html):
            // window.openPortfolioGate определена, только когда гейт активен, и к
            // моменту клика уже выполнена. У персонала её нет — ветка не срабатывает.
            var navKey = link.getAttribute('data-nav-key');
            if (window.openPortfolioGate && navKey !== 'learning' && navKey !== 'archive' && navKey !== 'personal') {
                e.preventDefault();
                setOpen(false);
                window.openPortfolioGate();
                return;
            }
            if (loader) loader.classList.add('active');
        });
    }

    // Скрипт подключается сразу после разметки меню, но DOMContentLoaded
    // надёжнее: партиал роли может оказаться выше своего <nav> при правках.
    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-burger-nav]'), function (nav) {
            if (nav.hasAttribute('data-burger-ready')) return;
            nav.setAttribute('data-burger-ready', '');
            init(nav);
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
