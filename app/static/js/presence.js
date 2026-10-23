/*
«Был на платформе» и время на платформе (Александрия 22.10.2026).

Пока вкладка ученика открыта и видна, шлём `POST /presence` сразу и потом раз
в минуту; скрытая вкладка (свёрнут браузер, другая вкладка, экран погас)
молчит. Время считает сервер по промежуткам между пингами
(`services/presence.py`), поэтому две открытые вкладки не удваивают минуты.

Подключается из `base.html` только ученику и не во входе «глазами».
Ключ CSRF — через общий `window.csrfFetch` (`csrf.js`). 401 и 403 — сессии
нет или путь закрыт: пинг замолкает до следующего показа вкладки.
*/
(function () {
    if (!window.csrfFetch) return;
    var PERIOD_MS = 60 * 1000;
    var timer = null;

    function stop() {
        if (timer) {
            clearInterval(timer);
            timer = null;
        }
    }

    function ping() {
        if (document.visibilityState !== 'visible') return;
        window.csrfFetch('/presence', {method: 'POST'}).then(function (resp) {
            if (resp.status === 401 || resp.status === 403) stop();
        }).catch(function () {});
    }

    function start() {
        if (timer) return;
        ping();
        timer = setInterval(ping, PERIOD_MS);
    }

    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') start();
        else stop();
    });
    if (document.visibilityState === 'visible') start();
})();
